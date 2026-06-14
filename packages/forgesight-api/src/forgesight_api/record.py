"""Exporter-facing value types: the immutable ``Record`` and lifecycle events.

The pipeline converts a live operation model (:mod:`forgesight_api.model`) into an
immutable :class:`Record` before it crosses the queue boundary. Exporters and
interceptors consume ``Record``s, never live objects — so one exporter can never
mutate state another exporter will see. Immutability is how fault isolation (P6)
is enforced structurally, not by convention.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from types import MappingProxyType

from .model import Kind, LLMCall, MCPCall, RunStatus, ToolCall

_NANOS_PER_MS = 1_000_000
_EMPTY: Mapping[str, object] = MappingProxyType({})


def _empty_attrs() -> Mapping[str, object]:
    return _EMPTY


@dataclass(frozen=True, slots=True)
class Record:
    """The immutable, exporter-facing snapshot of one operation's start/end."""

    kind: Kind
    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str  # span name (semconv mapping is the exporter's job)
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    attributes: Mapping[str, object] = field(default_factory=_empty_attrs)
    # At most one of the following is set, depending on `kind`:
    llm: LLMCall | None = None
    tool: ToolCall | None = None
    mcp: MCPCall | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.end_unix_nanos is None:
            return None
        return (self.end_unix_nanos - self.start_unix_nanos) / _NANOS_PER_MS


class ExportResult(Enum):
    """Outcome of a batch export. Mirrors OTel's ``SpanExportResult``."""

    SUCCESS = 0
    FAILURE = 1


class EventType(StrEnum):
    """Lifecycle event kinds (open set, FR-8)."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    LLM_EXECUTED = "llm_executed"
    TOOL_EXECUTED = "tool_executed"
    MCP_EXECUTED = "mcp_executed"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Delivered to every ``EventListener`` in order; carries the record it describes."""

    type: EventType
    run_id: str
    unix_nanos: int
    record: Record | None = None
    attributes: Mapping[str, object] = field(default_factory=_empty_attrs)
