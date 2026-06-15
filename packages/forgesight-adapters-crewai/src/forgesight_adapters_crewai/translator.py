"""Translate CrewAI event-bus events → SDK scopes.

CrewAI emits events as a crew runs: kickoff → workflow, each agent execution → an agent run,
each task → a step, plus LLM-call and tool-usage events. The events carry no run ids, so
nesting uses the SDK's contextvars via per-kind LIFO stacks (CrewAI runs them strictly
nested) — see :class:`~forgesight_core.ScopeBridge`. These translation methods take
duck-typed event objects and are fully unit-tested with fakes; the bus subscription is the
thin lazy edge in :mod:`forgesight_adapters_crewai.adapter`.
"""

from __future__ import annotations

from typing import Any

from forgesight_core import (
    LLMScope,
    RunScope,
    ScopeBridge,
    StepScope,
    ToolScope,
    WorkflowScope,
    get_runtime,
    in_tool_call,
)

_CREW = "crew"
_AGENT = "agent"
_TASK = "task"
_LLM = "llm"
_TOOL = "tool"


class CrewError(Exception):
    """Synthesised for a CrewAI ``*Failed`` / ``*Error`` event with no exception object."""


class _DeferredScope:
    """A no-op scope pushed when a tool span is deferred (no double-instrument) — keeps the
    per-kind stack balanced so the matching end event pops cleanly."""

    def __enter__(self) -> _DeferredScope:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def record_error(self, exc: BaseException, *, code: str | None = None) -> None:
        return None


class CrewAIEventTranslator:
    """Maps CrewAI events onto SDK instrumentation calls (an event-bus handler set)."""

    def __init__(self) -> None:
        self._bridge = ScopeBridge()

    def on_crew_start(self, source: Any, event: Any) -> None:
        name = _attr(event, "crew_name", "crew")
        self._bridge.enter_stacked(_CREW, WorkflowScope(get_runtime(), name=name))

    def on_crew_end(self, source: Any, event: Any) -> None:
        self._bridge.exit_stacked(_CREW, error=_event_error(event))

    def on_agent_start(self, source: Any, event: Any) -> None:
        self._bridge.enter_stacked(_AGENT, RunScope(get_runtime(), name=_agent_name(event)))

    def on_agent_end(self, source: Any, event: Any) -> None:
        self._bridge.exit_stacked(_AGENT, error=_event_error(event))

    def on_task_start(self, source: Any, event: Any) -> None:
        self._bridge.enter_stacked(
            _TASK, StepScope(get_runtime(), name=_attr(event, "task_name", "task"))
        )

    def on_task_end(self, source: Any, event: Any) -> None:
        self._bridge.exit_stacked(_TASK, error=_event_error(event))

    def on_llm_start(self, source: Any, event: Any) -> None:
        provider = _attr(event, "provider", "unknown")
        model = _attr(event, "model", "unknown")
        self._bridge.enter_stacked(_LLM, LLMScope(get_runtime(), provider=provider, model=model))

    def on_llm_end(self, source: Any, event: Any) -> None:
        scope = self._bridge.peek_stacked(_LLM)
        if isinstance(scope, LLMScope):
            inp, out = _llm_usage(event)
            if inp or out:
                scope.record_usage(input=inp, output=out)
        self._bridge.exit_stacked(_LLM, error=_event_error(event))

    def on_tool_start(self, source: Any, event: Any) -> None:
        if in_tool_call():  # inner span (MCP tools/call) already covers it — defer, no double span
            self._bridge.enter_stacked(_TOOL, _DeferredScope())
            return
        self._bridge.enter_stacked(
            _TOOL, ToolScope(get_runtime(), name=_attr(event, "tool_name", "tool"))
        )

    def on_tool_end(self, source: Any, event: Any) -> None:
        self._bridge.exit_stacked(_TOOL, error=_event_error(event))


def _attr(event: Any, name: str, default: str) -> str:
    value = getattr(event, name, None)
    return str(value) if value else default


def _agent_name(event: Any) -> str:
    agent = getattr(event, "agent", None)
    role = getattr(agent, "role", None) if agent is not None else None
    return str(role) if role else _attr(event, "agent_role", "agent")


def _event_error(event: Any) -> BaseException | None:
    err = getattr(event, "error", None)
    if isinstance(err, BaseException):
        return err
    if err is not None:
        return CrewError(str(err))
    if type(event).__name__.endswith(("Failed", "ErrorEvent", "Error")):
        return CrewError(type(event).__name__)
    return None


def _llm_usage(event: Any) -> tuple[int, int]:
    usage = getattr(event, "usage", None) or getattr(event, "token_usage", None) or {}
    get = usage.get if isinstance(usage, dict) else (lambda k, d=None: getattr(usage, k, d))
    inp = get("prompt_tokens", get("input_tokens", 0))
    out = get("completion_tokens", get("output_tokens", 0))
    return int(inp or 0), int(out or 0)
