"""The locked telemetry domain model.

These are the *builder-facing* types an agent or framework integrator works with
while an operation is in flight. The runtime (feat-002) fills terminal fields
(`status`, `end_unix_nanos`, `cost_usd`) on completion, then converts them to an
immutable :class:`~forgesight_api.record.Record` before export.

Every type here is part of the locked surface (ADR-0006): adding an optional field
with a safe default is a minor bump; removing/renaming a field is a major bump.
The one exception is :class:`Content`, which is **experimental** while the OTel
GenAI content-capture conventions settle — only the *gate* (off by default, P7)
is locked, not its shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

_NANOS_PER_MS = 1_000_000


def _duration_ms(start_unix_nanos: int, end_unix_nanos: int | None) -> float | None:
    """Milliseconds between start and end, or None while the operation is open."""
    if end_unix_nanos is None:
        return None
    return (end_unix_nanos - start_unix_nanos) / _NANOS_PER_MS


class RunStatus(StrEnum):
    """Terminal (and in-flight) status of a run, step, or call.

    A :class:`~enum.StrEnum` so it serialises to its value with no custom JSON
    encoder and compares equal to the wire string.
    """

    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    GUARDRAIL = "guardrail"


class Kind(StrEnum):
    """The kind of operation a span/record represents."""

    WORKFLOW = "workflow"
    AGENT = "agent"
    STEP = "step"
    LLM = "llm"
    TOOL = "tool"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts for one LLM call.

    Field names map deterministically onto the OTel GenAI token attributes. Frozen
    because usage is a fact about a completed call, never mutated after the fact.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    reasoning: int = 0

    @property
    def total(self) -> int:
        """Sum of all five token categories."""
        return self.input + self.output + self.cache_read + self.cache_creation + self.reasoning


@dataclass(slots=True)
class Content:
    """Opt-in captured message content (**experimental** in 0.1).

    Populated only when ``capture_content`` is enabled (P7, feat-008). The gate is
    locked; this container's exact shape tracks the GenAI content-capture
    migration and may change before 1.0.
    """

    input_messages: object | None = None
    output_messages: object | None = None
    system_instructions: object | None = None


@dataclass(slots=True)
class LLMCall:
    """One LLM interaction (chat / completion / embeddings)."""

    provider: str  # -> gen_ai.provider.name
    request_model: str  # -> gen_ai.request.model
    response_model: str | None = None  # -> gen_ai.response.model
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None  # -> forgesight.usage.cost_usd; None until priced
    finish_reasons: tuple[str, ...] = ()  # -> gen_ai.response.finish_reasons
    latency_ms: float | None = None
    time_to_first_chunk_ms: float | None = None
    response_id: str | None = None
    params: dict[str, object] = field(default_factory=dict)  # temperature, max_tokens, top_p, ...
    content: Content | None = None  # populated only when capture_content is on (experimental)


@dataclass(slots=True)
class ToolCall:
    """One tool invocation (function / REST / database / internal)."""

    name: str  # -> gen_ai.tool.name
    tool_type: str = "function"  # -> gen_ai.tool.type (open set)
    call_id: str | None = None  # -> gen_ai.tool.call.id
    description: str | None = None  # -> gen_ai.tool.description
    status: RunStatus = RunStatus.RUNNING
    duration_ms: float | None = None


@dataclass(slots=True)
class MCPCall:
    """One Model Context Protocol interaction."""

    server: str
    method: str  # -> mcp.method.name (e.g. tools/call)
    tool: str | None = None  # -> gen_ai.tool.name when method == tools/call
    session_id: str | None = None  # -> mcp.session.id
    protocol_version: str | None = None  # -> mcp.protocol.version
    status: RunStatus = RunStatus.RUNNING
    duration_ms: float | None = None


@dataclass(slots=True)
class Step:
    """One iteration / phase within a run (e.g. a ReAct turn). An INTERNAL span."""

    name: str
    kind: Kind = Kind.STEP
    status: RunStatus = RunStatus.RUNNING
    start_unix_nanos: int = 0
    end_unix_nanos: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        return _duration_ms(self.start_unix_nanos, self.end_unix_nanos)


@dataclass(slots=True)
class AgentRun:
    """One agent execution — the root of a run's trace."""

    agent_name: str
    agent_version: str | None
    run_id: str  # ULID
    context_id: str | None  # -> gen_ai.conversation.id when a real session exists
    trace_id: str  # W3C 16-byte (32 hex) trace id
    parent_run_id: str | None  # links nested / spawned runs
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    metadata: dict[str, object] = field(default_factory=dict)  # business metadata (FR-5)

    @property
    def duration_ms(self) -> float | None:
        return _duration_ms(self.start_unix_nanos, self.end_unix_nanos)


@dataclass(slots=True)
class WorkflowRun:
    """A multi-step orchestration that parents one or more agent runs / steps."""

    workflow_name: str
    run_id: str  # ULID
    trace_id: str
    parent_run_id: str | None
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        return _duration_ms(self.start_unix_nanos, self.end_unix_nanos)
