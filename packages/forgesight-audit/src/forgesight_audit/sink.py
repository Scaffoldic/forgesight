"""The ``AuditSink`` protocol, the query report, and ``verify()``.

``AuditSink`` is a **package-local** Protocol — a second projection of telemetry alongside
the exporters, not a fifth entry on the locked ``-api`` SPI surface (P5). Like an exporter,
``write()`` never raises into the run and never blocks the hot path (P6).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .chain import DEFAULT_ALGORITHM, compute_hash
from .model import AuditEvent, AuditQuery, VerifyResult


class AuditReport:
    """The result of an :class:`AuditQuery` — the matched events and their cost rollup."""

    __slots__ = ("_events",)

    def __init__(self, events: Sequence[AuditEvent]) -> None:
        self._events = tuple(events)

    def events(self) -> Sequence[AuditEvent]:
        return self._events

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def cost_usd_total(self) -> float:
        return float(sum(e.cost_usd or 0.0 for e in self._events))


@runtime_checkable
class AuditSink(Protocol):
    """A tamper-evident, append-only projection of telemetry. Drivers: jsonl, sqlite, otel,
    siem; custom via this Protocol."""

    def write(self, event: AuditEvent) -> None:
        """Append ``event`` to the chain. NEVER raises; NEVER blocks the hot path (P6)."""
        ...

    def query(self, q: AuditQuery) -> AuditReport:
        """Offline compliance query over the recorded log (off the hot path)."""
        ...

    def export(self, q: AuditQuery, to: str) -> None:
        """Write a bundle (JSONL + a ``.manifest.json`` carrying the head hash)."""
        ...

    def head_hash(self) -> str | None:
        """The latest chain hash (the anchor point for external notarization)."""
        ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...

    def shutdown(self, timeout_millis: int = 30_000) -> None: ...


def verify(sink: AuditSink, *, algorithm: str | None = None) -> VerifyResult:
    """Walk ``prev_hash``/``hash`` over the whole chain; detect alteration / deletion /
    reordering. Returns the first ``seq`` where the chain breaks, else ``intact=True``."""
    algo = algorithm or str(getattr(sink, "algorithm", DEFAULT_ALGORITHM))
    events = list(sink.query(AuditQuery()).events())
    prev: str | None = None
    expected_seq = 0
    for event in events:
        # 1. self-consistency: the event's own hash must match its content + claimed prev.
        if event.hash != compute_hash(event, event.prev_hash, algo):
            return VerifyResult(False, len(events), broken_at=event.seq, reason="altered")
        # 2. chain linkage: prev_hash must point at the actual predecessor, seq contiguous.
        if event.prev_hash != prev or event.seq != expected_seq:
            reason = "deleted" if event.seq > expected_seq else "reordered"
            return VerifyResult(False, len(events), broken_at=event.seq, reason=reason)
        prev = event.hash
        expected_seq = event.seq + 1
    return VerifyResult(True, len(events))
