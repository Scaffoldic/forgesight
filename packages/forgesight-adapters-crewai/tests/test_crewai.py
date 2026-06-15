"""Tests for the CrewAI adapter: event→span mapping, nesting, usage, conformance."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from forgesight_adapters_crewai import CrewAIAdapter, CrewAIEventTranslator
from forgesight_adapters_crewai.adapter import _EVENT_HANDLERS
from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, configure, reset_runtime, tool_call_active
from forgesight_core.testing.conformance import run_adapter_conformance


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        yield exporter
    finally:
        reset_runtime()


# --- fake CrewAI events (duck-typed; class name drives error detection) -------
def _event(name: str, **attrs: Any) -> Any:
    return type(name, (), attrs)()


class _Agent:
    role = "researcher"


# --- fake event bus for the adapter lifecycle --------------------------------
class FakeBus:
    def __init__(self) -> None:
        self.handlers: dict[Any, list[Any]] = {}
        self.offs: list[tuple[Any, Any]] = []

    def on(self, event_type: Any) -> Any:
        def deco(fn: Any) -> Any:
            self.handlers.setdefault(event_type, []).append(fn)
            return fn

        return deco

    def off(self, event_type: Any, handler: Any) -> None:
        self.offs.append((event_type, handler))


def _fake_event_types() -> dict[str, Any]:
    return {name: type(name, (), {}) for name in _EVENT_HANDLERS}


# --- translation: canonical crew run -----------------------------------------
def test_canonical_crew_run_maps_to_span_tree(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_crew_start(None, _event("CrewKickoffStartedEvent", crew_name="my-crew"))
    t.on_agent_start(None, _event("AgentExecutionStartedEvent", agent=_Agent()))
    t.on_task_start(None, _event("TaskStartedEvent", task_name="research"))
    t.on_llm_start(None, _event("LLMCallStartedEvent", provider="openai", model="gpt-4o"))
    t.on_llm_end(
        None, _event("LLMCallCompletedEvent", usage={"prompt_tokens": 12, "completion_tokens": 4})
    )
    t.on_tool_start(None, _event("ToolUsageStartedEvent", tool_name="search"))
    t.on_tool_end(None, _event("ToolUsageFinishedEvent"))
    t.on_task_end(None, _event("TaskCompletedEvent"))
    t.on_agent_end(None, _event("AgentExecutionCompletedEvent"))
    t.on_crew_end(None, _event("CrewKickoffCompletedEvent"))

    by_kind = {r.kind: r for r in sink.records}
    assert set(by_kind) == {Kind.WORKFLOW, Kind.AGENT, Kind.STEP, Kind.LLM, Kind.TOOL}
    wf, agent, step = by_kind[Kind.WORKFLOW], by_kind[Kind.AGENT], by_kind[Kind.STEP]
    llm, tool = by_kind[Kind.LLM], by_kind[Kind.TOOL]
    assert wf.name == "my-crew"
    assert agent.name == "researcher"
    assert step.name == "research"
    assert agent.parent_span_id == wf.span_id  # agent under crew
    assert step.parent_span_id == agent.span_id  # task under agent
    assert llm.parent_span_id == step.span_id
    assert tool.parent_span_id == step.span_id
    assert llm.llm is not None
    assert llm.llm.provider == "openai"
    assert llm.llm.usage.input == 12
    assert llm.llm.usage.output == 4
    assert tool.tool is not None
    assert tool.tool.name == "search"


def test_agent_role_fallback(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_agent_start(None, _event("AgentExecutionStartedEvent", agent_role="planner"))
    t.on_agent_end(None, _event("AgentExecutionCompletedEvent"))
    agent = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert agent.name == "planner"


def test_llm_usage_object_attributes(sink: InMemoryExporter) -> None:
    class _Usage:
        prompt_tokens = 9
        completion_tokens = 2

    t = CrewAIEventTranslator()
    t.on_llm_start(None, _event("LLMCallStartedEvent", model="m"))
    t.on_llm_end(None, _event("LLMCallCompletedEvent", usage=_Usage()))
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.llm is not None
    assert llm.llm.usage.input == 9
    assert llm.llm.usage.output == 2
    assert llm.llm.provider == "unknown"  # no provider on the event


def test_failed_event_marks_error(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_agent_start(None, _event("AgentExecutionStartedEvent", agent=_Agent()))
    t.on_agent_end(None, _event("AgentExecutionErrorEvent"))  # name ends "Error" ⇒ error
    agent = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert agent.status is RunStatus.ERROR
    assert agent.error is not None
    assert agent.error.error_type == "CrewError"  # synthesised; message carries the event name
    assert "AgentExecutionErrorEvent" in agent.error.message


def test_error_attribute_with_exception(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_tool_start(None, _event("ToolUsageStartedEvent", tool_name="search"))
    t.on_tool_end(None, _event("ToolUsageErrorEvent", error=ValueError("bad input")))
    tool = next(r for r in sink.records if r.kind is Kind.TOOL)
    assert tool.status is RunStatus.ERROR
    assert tool.error is not None
    assert tool.error.error_type == "ValueError"


def test_error_attribute_with_string(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_tool_start(None, _event("ToolUsageStartedEvent", tool_name="x"))
    t.on_tool_end(None, _event("ToolUsageFinishedEvent", error="timed out"))
    tool = next(r for r in sink.records if r.kind is Kind.TOOL)
    assert tool.status is RunStatus.ERROR
    assert tool.error is not None
    assert tool.error.error_type == "CrewError"


# --- no double-instrument -----------------------------------------------------
def test_defers_to_inner_tool_span(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_crew_start(None, _event("CrewKickoffStartedEvent", crew_name="c"))
    with tool_call_active():  # MCP tools/call already covers this tool
        t.on_tool_start(None, _event("ToolUsageStartedEvent", tool_name="search"))
        t.on_tool_end(None, _event("ToolUsageFinishedEvent"))
    t.on_crew_end(None, _event("CrewKickoffCompletedEvent"))
    assert [r for r in sink.records if r.kind is Kind.TOOL] == []  # deferred, balanced stack


# --- adapter lifecycle (fake bus, no crewai) ---------------------------------
def test_conformance() -> None:
    run_adapter_conformance(
        lambda: CrewAIAdapter(event_bus=FakeBus(), event_types=_fake_event_types())
    )


def test_adapter_name() -> None:
    assert CrewAIAdapter().name == "crewai"


def test_instrument_subscribes_and_uninstrument_unsubscribes() -> None:
    bus = FakeBus()
    adapter = CrewAIAdapter(event_bus=bus, event_types=_fake_event_types())
    adapter.instrument()
    assert len(bus.handlers) == len(set(_fake_event_types().values()))  # one handler per event type
    adapter.uninstrument()
    assert len(bus.offs) == len(_EVENT_HANDLERS)  # every registration removed


def test_missing_event_type_is_skipped() -> None:
    bus = FakeBus()
    # only a subset of event types available ⇒ the rest are skipped, not an error
    partial = {"CrewKickoffStartedEvent": type("CrewKickoffStartedEvent", (), {})}
    adapter = CrewAIAdapter(event_bus=bus, event_types=partial)
    adapter.instrument()
    assert len(bus.handlers) == 1
    adapter.uninstrument()


def test_translator_property() -> None:
    adapter = CrewAIAdapter(event_bus=FakeBus(), event_types=_fake_event_types())
    assert isinstance(adapter.translator, CrewAIEventTranslator)


def test_unsubscribe_bus_without_off_is_noop() -> None:
    class BusNoOff:
        def on(self, event_type: Any) -> Any:
            return lambda fn: fn

    adapter = CrewAIAdapter(event_bus=BusNoOff(), event_types=_fake_event_types())
    adapter.instrument()
    adapter.uninstrument()  # bus has no off() ⇒ must not raise
    assert adapter.is_instrumented() is False


def test_llm_end_without_start_and_no_usage(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    t.on_llm_end(None, _event("LLMCallCompletedEvent"))  # no matching start ⇒ no-op, no raise
    assert [r for r in sink.records if r.kind is Kind.LLM] == []
    # a real llm with no usage on the event ⇒ recorded, no usage (cost stays null)
    t.on_llm_start(None, _event("LLMCallStartedEvent", model="m"))
    t.on_llm_end(None, _event("LLMCallCompletedEvent"))
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.llm is not None
    assert llm.llm.usage.input == 0


def test_deferred_tool_with_error_end(sink: InMemoryExporter) -> None:
    t = CrewAIEventTranslator()
    with tool_call_active():
        t.on_tool_start(None, _event("ToolUsageStartedEvent", tool_name="x"))
        t.on_tool_end(None, _event("ToolUsageErrorEvent", error=ValueError("boom")))
    assert [
        r for r in sink.records if r.kind is Kind.TOOL
    ] == []  # deferred; error swallowed cleanly
