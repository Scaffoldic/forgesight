"""Shared chaining machinery for the audit sinks.

``_ChainedSink`` owns the integrity contract: assign a monotonic ``seq``, fold in the
previous hash, compute this event's hash, then hand the chained event to the driver's
``_append``. ``write()`` is contractually non-raising (P6) — a driver failure is counted,
never propagated into the run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import replace

from ..chain import SUPPORTED_ALGORITHMS, compute_hash
from ..model import AuditEvent, AuditQuery
from ..sink import AuditReport

_LOG = logging.getLogger("forgesight.audit")


class _ChainedSink:
    """Base for durable, hash-chained audit sinks. Subclasses implement ``_append`` /
    ``_read_all`` over their medium and call ``_bootstrap()`` once it is ready."""

    def __init__(self, *, algorithm: str = "sha256") -> None:
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"unsupported hash algorithm {algorithm!r}; expected one of "
                f"{sorted(SUPPORTED_ALGORITHMS)}"
            )
        self.algorithm = algorithm
        self.write_failures = 0
        self._head_hash: str | None = None
        self._next_seq = 0
        self._closed = False

    # --- chain writer (single point that assigns seq/prev/hash) -----------------
    def write(self, event: AuditEvent) -> None:
        if self._closed:
            return
        try:
            chained = replace(event, seq=self._next_seq, prev_hash=self._head_hash, hash="")
            digest = compute_hash(chained, self._head_hash, self.algorithm)
            chained = replace(chained, hash=digest)
            self._append(chained)
            self._head_hash = digest
            self._next_seq += 1
        except Exception:  # never raise into the run (P6)
            _LOG.exception("audit write failed for sink %s", type(self).__name__)
            self.write_failures += 1

    def head_hash(self) -> str | None:
        return self._head_hash

    def query(self, q: AuditQuery) -> AuditReport:
        return AuditReport([e for e in self._read_all() if q.matches(e)])

    def export(self, q: AuditQuery, to: str) -> None:
        events = list(self.query(q).events())
        with open(to, "w", encoding="utf-8") as bundle:
            for event in events:
                bundle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        manifest = {
            "algorithm": self.algorithm,
            "head_hash": self.head_hash(),
            "event_count": len(events),
            "schema_version": 1,
        }
        with open(to + ".manifest.json", "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._closed = True

    # --- bootstrap + driver hooks ----------------------------------------------
    def _bootstrap(self) -> None:
        """Resume the chain from any already-recorded events (head hash + next seq)."""
        existing = list(self._read_all())
        if existing:
            last = existing[-1]
            self._head_hash = last.hash
            self._next_seq = last.seq + 1

    def _append(self, event: AuditEvent) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _read_all(self) -> Sequence[AuditEvent]:  # pragma: no cover - abstract
        raise NotImplementedError


class _BridgeSink(_ChainedSink):
    """Base for write-through bridge sinks (otel, siem): keep an in-process chain for
    this-session ``query``/``verify``, and bridge each event out via ``_emit``. An emit
    failure is isolated — it never corrupts the chain or raises into the run."""

    def __init__(self, *, algorithm: str = "sha256") -> None:
        self._events: list[AuditEvent] = []
        self.emit_failures = 0
        super().__init__(algorithm=algorithm)
        self._bootstrap()  # in-memory backing always starts empty

    def _append(self, event: AuditEvent) -> None:
        self._events.append(event)  # commit to the chain first
        try:
            self._emit(event)
        except Exception:  # bridge failure must not corrupt the chain (P6)
            _LOG.exception("audit bridge emit failed for sink %s", type(self).__name__)
            self.emit_failures += 1

    def _read_all(self) -> Sequence[AuditEvent]:
        return tuple(self._events)

    def _emit(self, event: AuditEvent) -> None:  # pragma: no cover - abstract
        raise NotImplementedError
