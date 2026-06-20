"""The audit-event taxonomy, the immutable ``AuditEvent``, and the query/verify shapes.

These are the data contracts of the audit projection (feat-023). The ``AuditEvent``
canonical serialization + ``prev_hash``/``hash`` rule (see ``chain.py``) is the one part
treated as stable-from-ship — changing it would invalidate existing logs' ``verify()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1


class AuditKind(StrEnum):
    """The stable ``AuditEvent`` taxonomy. Open set: new kinds may be appended in a minor
    (P5); a consumer ignores kinds it does not know."""

    RUN_START = "run.start"
    RUN_END = "run.end"
    MODEL_CALL = "model.call"
    TOOL_CALL = "tool.call"
    ERROR = "error"
    POLICY_DECISION = "policy.decision"  # only when feat-020 governance is installed
    BUDGET_EVENT = "budget.event"  # only when feat-020 governance is installed


DEFAULT_KINDS: tuple[AuditKind, ...] = tuple(AuditKind)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only, hash-chained audit record — attributed, cost-stamped, chained.

    ``seq`` / ``prev_hash`` / ``hash`` are assigned by the sink's single writer at
    ``write()`` time; a freshly built event leaves them at their defaults.
    """

    kind: AuditKind
    timestamp_unix_nanos: int
    run_id: str
    trace_id: str
    principal: str
    version: str | None = None
    owner: str | None = None
    team: str | None = None
    cost_usd: float | None = None
    status: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    seq: int = -1  # monotonic chain position; assigned at write()
    prev_hash: str | None = None  # hash of the predecessor (None for seq 0)
    hash: str = ""  # hash over the canonical serialization of THIS event + prev_hash
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The persisted form (includes ``seq``/``prev_hash``/``hash``)."""
        return {
            "schema_version": self.schema_version,
            "kind": str(self.kind),
            "seq": self.seq,
            "timestamp_unix_nanos": self.timestamp_unix_nanos,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "principal": self.principal,
            "version": self.version,
            "owner": self.owner,
            "team": self.team,
            "cost_usd": self.cost_usd,
            "status": self.status,
            "attributes": dict(self.attributes),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuditEvent:
        attrs = data.get("attributes") or {}
        return cls(
            kind=AuditKind(data["kind"]),
            timestamp_unix_nanos=int(data["timestamp_unix_nanos"]),
            run_id=str(data["run_id"]),
            trace_id=str(data["trace_id"]),
            principal=str(data["principal"]),
            version=data.get("version"),
            owner=data.get("owner"),
            team=data.get("team"),
            cost_usd=data.get("cost_usd"),
            status=data.get("status"),
            attributes={str(k): str(v) for k, v in dict(attrs).items()},
            seq=int(data.get("seq", -1)),
            prev_hash=data.get("prev_hash"),
            hash=str(data.get("hash", "")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """A compliance query over the recorded audit log. An empty query matches every event."""

    principal: str | None = None
    team: str | None = None
    kind: AuditKind | None = None
    since: int | None = None  # unix nanos, inclusive
    until: int | None = None  # unix nanos, exclusive

    def matches(self, event: AuditEvent) -> bool:
        if self.principal is not None and event.principal != self.principal:
            return False
        if self.team is not None and event.team != self.team:
            return False
        if self.kind is not None and event.kind != self.kind:
            return False
        if self.since is not None and event.timestamp_unix_nanos < self.since:
            return False
        return not (self.until is not None and event.timestamp_unix_nanos >= self.until)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """The outcome of walking a log's hash chain."""

    intact: bool
    event_count: int
    broken_at: int | None = None  # the seq where the chain first failed, else None
    reason: str | None = None  # "altered" | "deleted" | "reordered" | None
