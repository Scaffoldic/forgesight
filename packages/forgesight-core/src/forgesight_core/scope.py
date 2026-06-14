"""Instrumentation scopes — the context managers that build the span tree.

Each scope, on enter, mints a ``span_id``, reads the active :class:`TelemetryContext`
for its parent, binds itself as the new current context, and emits a *started* event.
On exit it fills status + timing, builds an immutable :class:`Record`, emits the
matching *executed*/*completed*/*failed* event, hands the record to the runtime, and
restores the previous context. All scopes are **both** sync and async context managers.

The hot path is CPU-bound only: build the record + run interceptors + hand off. No
network, no awaiting an exporter (P6, NFR-2) — the actual export is the runtime's
(feat-003) job.
"""

from __future__ import annotations

import contextvars
import time
import traceback
from dataclasses import replace
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Literal

from forgesight_api import (
    ErrorInfo,
    EventType,
    Kind,
    LifecycleEvent,
    LLMCall,
    MCPCall,
    Record,
    RunStatus,
    TokenUsage,
    ToolCall,
)

from .context import (
    TelemetryContext,
    current_context,
    new_run_id,
    new_span_id,
    reset_current_context,
    set_current_context,
)

if TYPE_CHECKING:
    from .processor import Runtime, RuntimeConfig

_NANOS_PER_MS = 1_000_000
_STEP_EVENTS = frozenset({EventType.STEP_STARTED, EventType.STEP_COMPLETED})

_CURRENT_RUN: contextvars.ContextVar[RunScope | None] = contextvars.ContextVar(
    "forgesight_current_run", default=None
)


def current_run_scope() -> RunScope | None:
    """Return the active :class:`RunScope`, or ``None`` outside any run."""
    return _CURRENT_RUN.get()


def _now() -> int:
    return time.time_ns()


class _Scope:
    """Shared enter/exit machinery: timing, span id, context bind, status, events."""

    _start_event: EventType | None = None
    _finish_event: EventType | None = None

    def __init__(self, runtime: Runtime, *, name: str, kind: Kind) -> None:
        self._rt = runtime
        self.name = name
        self.kind = kind
        self.span_id = ""
        self.run_id = ""
        self.trace_id = ""
        self.parent_span_id: str | None = None
        self._status = RunStatus.RUNNING
        self._start = 0
        self._end: int | None = None
        self._parent_ctx: TelemetryContext | None = None
        self._bound_ctx: TelemetryContext | None = None
        self._token: contextvars.Token[TelemetryContext | None] | None = None
        self._error: ErrorInfo | None = None

    def record_error(self, exc: BaseException, *, code: str | None = None) -> None:
        """Capture ``exc`` onto this scope and mark it errored — does NOT re-raise.

        For caught-and-handled paths. The context managers call the equivalent on
        ``__exit__`` and then re-raise (FR-7).
        """
        self._error = _error_info(exc, code, self._rt.config)
        self._status = RunStatus.ERROR

    # --- context construction: overridden by container vs leaf -------------
    def _make_context(self, parent: TelemetryContext | None) -> TelemetryContext:
        raise NotImplementedError

    def _inherited_metadata(self) -> dict[str, object]:
        """The metadata that should appear on this scope's own record."""
        assert self._bound_ctx is not None
        return dict(self._bound_ctx.metadata)

    def _build_record(self) -> Record:
        raise NotImplementedError

    # --- lifecycle --------------------------------------------------------
    def _enter(self) -> None:
        self._start = _now()
        self.span_id = new_span_id()
        self._parent_ctx = current_context()
        self.parent_span_id = self._parent_ctx.current_span_id if self._parent_ctx else None
        self._bound_ctx = self._make_context(self._parent_ctx)
        self.run_id = self._bound_ctx.run_id
        self.trace_id = self._bound_ctx.trace_id
        self._token = set_current_context(self._bound_ctx)
        if self._start_event is not None and self._emit_enabled(self._start_event):
            self._rt.emit_event(
                LifecycleEvent(
                    type=self._start_event,
                    run_id=self.run_id,
                    unix_nanos=self._start,
                    trace_id=self.trace_id,
                    span_id=self.span_id,
                )
            )

    def _exit(self, exc: BaseException | None) -> None:
        self._end = _now()
        if self._status is RunStatus.RUNNING:
            self._status = RunStatus.ERROR if exc is not None else RunStatus.OK
        if exc is not None and self._error is None:
            self._error = _error_info(exc, None, self._rt.config)
        try:
            record = self._build_record()
            if self._error is not None:
                record = replace(record, error=self._error)
            self._rt.emit_record(record)
            if self._finish_event is not None:
                event_type = self._finish_event
                if exc is not None and event_type is EventType.RUN_COMPLETED:
                    event_type = EventType.RUN_FAILED
                if self._emit_enabled(event_type):
                    self._rt.emit_event(
                        LifecycleEvent(
                            type=event_type,
                            run_id=self.run_id,
                            unix_nanos=self._end,
                            record=record,
                            attributes=record.attributes,
                            trace_id=self.trace_id,
                            span_id=self.span_id,
                        )
                    )
        finally:
            if self._token is not None:
                reset_current_context(self._token)

    def __enter__(self) -> _Scope:
        self._enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self._exit(exc)
        return False  # never swallow the caller's exception (FR-7)

    async def __aenter__(self) -> _Scope:
        self._enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self._exit(exc)
        return False

    def _frozen_attrs(self, attrs: dict[str, object]) -> MappingProxyType[str, object]:
        return MappingProxyType(dict(attrs))

    def _emit_enabled(self, event_type: EventType) -> bool:
        if event_type in _STEP_EVENTS:
            return self._rt.config.deliver_step_events
        return True


class _ContainerScope(_Scope):
    """A run / workflow / step — opens child scopes and propagates metadata to them."""

    def set_metadata(self, **kv: object) -> None:
        """Attach metadata at this scope; inherited by every child span (FR-5)."""
        assert self._bound_ctx is not None
        self._bound_ctx.metadata.update(kv)

    def step(self, name: str, *, metadata: dict[str, object] | None = None) -> StepScope:
        return StepScope(self._rt, name=name, metadata=metadata)

    def llm_call(
        self, provider: str, model: str, *, metadata: dict[str, object] | None = None
    ) -> LLMScope:
        return LLMScope(self._rt, provider=provider, model=model, metadata=metadata)

    def tool_call(
        self,
        name: str,
        *,
        tool_type: str | None = None,
        call_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ToolScope:
        return ToolScope(
            self._rt,
            name=name,
            tool_type=tool_type or self._rt.config.default_tool_type,
            call_id=call_id,
            metadata=metadata,
        )

    def mcp_call(
        self,
        server: str,
        method: str,
        *,
        tool: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MCPScope:
        return MCPScope(
            self._rt,
            server=server,
            method=method,
            tool=tool,
            session_id=session_id,
            metadata=metadata,
        )


class RunScope(_ContainerScope):
    """One agent execution — the root of a run's trace."""

    _start_event = EventType.RUN_STARTED
    _finish_event = EventType.RUN_COMPLETED

    def __init__(
        self,
        runtime: Runtime,
        *,
        name: str,
        version: str | None = None,
        parent_run_id: str | None = None,
        context_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(runtime, name=name, kind=Kind.AGENT)
        self.version = version
        self._explicit_parent_run_id = parent_run_id
        self._context_id = context_id
        self._init_metadata = dict(metadata or {})
        self.parent_run_id: str | None = None
        self._run_token: contextvars.Token[RunScope | None] | None = None

    def _enter(self) -> None:
        super()._enter()
        self._run_token = _CURRENT_RUN.set(self)

    def _exit(self, exc: BaseException | None) -> None:
        try:
            super()._exit(exc)
        finally:
            if self._run_token is not None:
                _CURRENT_RUN.reset(self._run_token)

    def _make_context(self, parent: TelemetryContext | None) -> TelemetryContext:
        from forgesight_api import new_trace_id

        run_id = new_run_id()
        trace_id = parent.trace_id if parent is not None else new_trace_id()
        self.parent_run_id = self._explicit_parent_run_id or (
            parent.run_id if parent is not None else None
        )
        metadata: dict[str, object] = dict(parent.metadata) if parent is not None else {}
        metadata.update(self._init_metadata)
        return TelemetryContext(
            run_id=run_id,
            trace_id=trace_id,
            parent_run_id=self.parent_run_id,
            current_span_id=self.span_id,
            context_id=self._context_id or (parent.context_id if parent else None),
            metadata=metadata,
        )

    def _build_record(self) -> Record:
        attrs = self._inherited_metadata()
        if self.version is not None:
            attrs["agent.version"] = self.version
        if self.parent_run_id is not None:
            attrs["parent.run_id"] = self.parent_run_id
        if self._context_id is not None:
            attrs["context.id"] = self._context_id
        return Record(
            kind=Kind.AGENT,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(attrs),
        )


class WorkflowScope(_ContainerScope):
    """A multi-step orchestration that parents one or more agent runs / steps."""

    _start_event = EventType.RUN_STARTED
    _finish_event = EventType.RUN_COMPLETED

    def __init__(
        self, runtime: Runtime, *, name: str, metadata: dict[str, object] | None = None
    ) -> None:
        super().__init__(runtime, name=name, kind=Kind.WORKFLOW)
        self._init_metadata = dict(metadata or {})

    def _make_context(self, parent: TelemetryContext | None) -> TelemetryContext:
        from forgesight_api import new_trace_id

        run_id = new_run_id()
        trace_id = parent.trace_id if parent is not None else new_trace_id()
        metadata: dict[str, object] = dict(parent.metadata) if parent is not None else {}
        metadata.update(self._init_metadata)
        return TelemetryContext(
            run_id=run_id,
            trace_id=trace_id,
            parent_run_id=parent.run_id if parent is not None else None,
            current_span_id=self.span_id,
            context_id=parent.context_id if parent is not None else None,
            metadata=metadata,
        )

    def _build_record(self) -> Record:
        return Record(
            kind=Kind.WORKFLOW,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(self._inherited_metadata()),
        )


class StepScope(_ContainerScope):
    """One iteration / phase within a run. Steps may nest."""

    _start_event = EventType.STEP_STARTED
    _finish_event = EventType.STEP_COMPLETED

    def __init__(
        self, runtime: Runtime, *, name: str, metadata: dict[str, object] | None = None
    ) -> None:
        super().__init__(runtime, name=name, kind=Kind.STEP)
        self._init_metadata = dict(metadata or {})

    def _make_context(self, parent: TelemetryContext | None) -> TelemetryContext:
        if parent is None:
            from forgesight_api import new_trace_id

            base = TelemetryContext(run_id=new_run_id(), trace_id=new_trace_id())
        else:
            base = parent
        ctx = base.child(current_span_id=self.span_id)
        ctx.metadata.update(self._init_metadata)
        return ctx

    def _build_record(self) -> Record:
        return Record(
            kind=Kind.STEP,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(self._inherited_metadata()),
        )


class _LeafScope(_Scope):
    """A leaf call (LLM / tool / MCP). Owns per-call metadata; inherits the rest."""

    def __init__(self, runtime: Runtime, *, name: str, kind: Kind) -> None:
        super().__init__(runtime, name=name, kind=kind)
        self._own_md: dict[str, object] = {}

    def set_metadata(self, **kv: object) -> None:
        """Attach metadata to this call only (not inherited by anything — FR-5)."""
        self._own_md.update(kv)

    def _make_context(self, parent: TelemetryContext | None) -> TelemetryContext:
        if parent is None:
            from forgesight_api import new_trace_id

            return TelemetryContext(
                run_id=new_run_id(), trace_id=new_trace_id(), current_span_id=self.span_id
            )
        return parent.child(current_span_id=self.span_id)

    def _merged_attrs(self) -> dict[str, object]:
        inherited = dict(self._parent_ctx.metadata) if self._parent_ctx is not None else {}
        inherited.update(self._own_md)  # call-scope wins on conflict
        return inherited


class LLMScope(_LeafScope):
    """One LLM interaction. Records usage/response/params; priced on exit (feat-006)."""

    _finish_event = EventType.LLM_EXECUTED

    def __init__(
        self,
        runtime: Runtime,
        *,
        provider: str,
        model: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(runtime, name=model, kind=Kind.LLM)
        self._call = LLMCall(provider=provider, request_model=model)
        if metadata:
            self._own_md.update(metadata)

    def record_usage(
        self,
        *,
        input: int = 0,
        output: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
        reasoning: int = 0,
    ) -> None:
        self._call.usage = TokenUsage(
            input=input,
            output=output,
            cache_read=cache_read,
            cache_creation=cache_creation,
            reasoning=reasoning,
        )

    def record_response(
        self,
        *,
        response_model: str | None = None,
        finish_reasons: tuple[str, ...] = (),
        response_id: str | None = None,
        time_to_first_chunk_ms: float | None = None,
    ) -> None:
        self._call.response_model = response_model
        self._call.finish_reasons = finish_reasons
        self._call.response_id = response_id
        self._call.time_to_first_chunk_ms = time_to_first_chunk_ms

    def record_params(self, **params: object) -> None:
        self._call.params.update(params)

    def set_cost(self, cost_usd: float) -> None:
        """Provider-supplied cost — takes precedence over computed pricing (FR-9)."""
        self._call.cost_usd = cost_usd

    def _build_record(self) -> Record:
        self._call.latency_ms = self.duration_ms
        if self._call.cost_usd is None and self._rt.pricing is not None:
            self._call.cost_usd = self._rt.pricing.price(
                self._call.provider, self._call.request_model, self._call.usage
            )
        return Record(
            kind=Kind.LLM,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self._call.request_model,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(self._merged_attrs()),
            llm=self._call,
        )

    @property
    def duration_ms(self) -> float | None:
        if self._end is None:
            return None
        return (self._end - self._start) / _NANOS_PER_MS


class ToolScope(_LeafScope):
    """One tool invocation."""

    _finish_event = EventType.TOOL_EXECUTED

    def __init__(
        self,
        runtime: Runtime,
        *,
        name: str,
        tool_type: str = "function",
        call_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(runtime, name=name, kind=Kind.TOOL)
        self._call = ToolCall(name=name, tool_type=tool_type, call_id=call_id)
        if metadata:
            self._own_md.update(metadata)

    def _build_record(self) -> Record:
        self._call.status = self._status
        self._call.duration_ms = _ms(self._start, self._end)
        return Record(
            kind=Kind.TOOL,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self._call.name,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(self._merged_attrs()),
            tool=self._call,
        )


class MCPScope(_LeafScope):
    """One MCP interaction."""

    _finish_event = EventType.MCP_EXECUTED

    def __init__(
        self,
        runtime: Runtime,
        *,
        server: str,
        method: str,
        tool: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(runtime, name=method, kind=Kind.MCP)
        self._call = MCPCall(server=server, method=method, tool=tool, session_id=session_id)
        if metadata:
            self._own_md.update(metadata)

    def _build_record(self) -> Record:
        self._call.status = self._status
        self._call.duration_ms = _ms(self._start, self._end)
        return Record(
            kind=Kind.MCP,
            run_id=self.run_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self._call.method,
            status=self._status,
            start_unix_nanos=self._start,
            end_unix_nanos=self._end,
            attributes=self._frozen_attrs(self._merged_attrs()),
            mcp=self._call,
        )


def _ms(start: int, end: int | None) -> float | None:
    if end is None:
        return None
    return (end - start) / _NANOS_PER_MS


def _error_info(exc: BaseException, code: str | None, config: RuntimeConfig) -> ErrorInfo:
    stacktrace: str | None = None
    if config.capture_stacktrace and config.stack_capture_depth > 0:
        stacktrace = "".join(
            traceback.format_exception(
                type(exc), exc, exc.__traceback__, limit=config.stack_capture_depth
            )
        )
    resolved_code = code
    if resolved_code is None:
        raw = getattr(exc, "code", None)
        if raw is not None:
            resolved_code = str(raw)
    return ErrorInfo(
        error_type=type(exc).__name__,
        message=str(exc),
        stacktrace=stacktrace,
        code=resolved_code,
    )
