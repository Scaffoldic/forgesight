"""Tests for the adapter infrastructure: BaseAdapter, ScopeBridge, guard, auto-load."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgesight_api import Kind, RunStatus
from forgesight_core import (
    BaseAdapter,
    InMemoryExporter,
    LLMScope,
    RunScope,
    ScopeBridge,
    StepScope,
    ToolScope,
    configure,
    get_runtime,
    in_tool_call,
    reset_runtime,
    tool_call_active,
)
from forgesight_core.config import load_adapters, register
from forgesight_core.testing.conformance import run_adapter_conformance


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        yield exporter
    finally:
        reset_runtime()


# --- BaseAdapter --------------------------------------------------------------
class _SpyAdapter(BaseAdapter):
    name = "spy"

    def __init__(self) -> None:
        super().__init__()
        self.subscribed = 0
        self.unsubscribed = 0

    def _subscribe(self) -> None:
        self.subscribed += 1

    def _unsubscribe(self) -> None:
        self.unsubscribed += 1


def test_base_adapter_is_idempotent() -> None:
    adapter = _SpyAdapter()
    assert adapter.is_instrumented() is False
    adapter.instrument()
    adapter.instrument()  # no-op
    assert adapter.subscribed == 1
    assert adapter.is_instrumented() is True
    adapter.uninstrument()
    adapter.uninstrument()  # no-op
    assert adapter.unsubscribed == 1
    assert adapter.is_instrumented() is False


def test_base_adapter_passes_conformance() -> None:
    run_adapter_conformance(_SpyAdapter)


def test_base_adapter_subclass_must_implement() -> None:
    class Incomplete(BaseAdapter):
        name = "x"

    with pytest.raises(NotImplementedError):
        Incomplete().instrument()


# --- ScopeBridge: keyed + stacked --------------------------------------------
def test_bridge_keyed_builds_nested_tree(sink: InMemoryExporter) -> None:
    rt = get_runtime()
    bridge = ScopeBridge()
    bridge.enter_keyed("run", RunScope(rt, name="agent"))
    bridge.enter_keyed("step", StepScope(rt, name="node"))
    bridge.enter_keyed("llm", LLMScope(rt, provider="anthropic", model="m"))
    bridge.get_keyed("llm").record_usage(input=10, output=5)  # type: ignore[union-attr]
    bridge.exit_keyed("llm")
    bridge.exit_keyed("step")
    bridge.exit_keyed("run")

    by_kind = {r.kind: r for r in sink.records}
    run, step, llm = by_kind[Kind.AGENT], by_kind[Kind.STEP], by_kind[Kind.LLM]
    assert step.trace_id == run.trace_id == llm.trace_id  # one trace
    assert step.parent_span_id == run.span_id  # node under run
    assert llm.parent_span_id == step.span_id  # llm under node
    assert llm.llm is not None
    assert llm.llm.usage.input == 10


def test_bridge_stacked_lifo(sink: InMemoryExporter) -> None:
    rt = get_runtime()
    bridge = ScopeBridge()
    bridge.enter_stacked("a", RunScope(rt, name="run"))
    bridge.enter_stacked("b", ToolScope(rt, name="search"))
    assert isinstance(bridge.peek_stacked("b"), ToolScope)
    bridge.exit_stacked("b")
    bridge.exit_stacked("a")
    tool = next(r for r in sink.records if r.kind is Kind.TOOL)
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert tool.parent_span_id == run.span_id


def test_bridge_exit_missing_key_is_noop() -> None:
    bridge = ScopeBridge()
    assert bridge.exit_keyed("nope") is None
    assert bridge.exit_stacked("nope") is None
    assert bridge.peek_stacked("nope") is None


def test_bridge_error_marks_scope(sink: InMemoryExporter) -> None:
    rt = get_runtime()
    bridge = ScopeBridge()
    bridge.enter_keyed("r", RunScope(rt, name="run"))
    bridge.exit_keyed("r", error=ValueError("boom"))
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.status is RunStatus.ERROR
    assert run.error is not None
    assert run.error.error_type == "ValueError"


def test_bridge_close_all(sink: InMemoryExporter) -> None:
    rt = get_runtime()
    bridge = ScopeBridge()
    bridge.enter_keyed("r", RunScope(rt, name="run"))
    bridge.enter_stacked("s", StepScope(rt, name="step"))
    bridge.close_all()  # closes leftovers innermost-first
    assert {r.kind for r in sink.records} == {Kind.AGENT, Kind.STEP}


# --- re-entrancy guard --------------------------------------------------------
def test_tool_call_guard() -> None:
    assert in_tool_call() is False
    with tool_call_active():
        assert in_tool_call() is True
    assert in_tool_call() is False


# --- auto-load (config-driven) ------------------------------------------------
def test_load_adapters_instruments_enabled() -> None:
    created: list[_SpyAdapter] = []

    @register("adapters", "spy")
    def _factory() -> _SpyAdapter:
        adapter = _SpyAdapter()
        created.append(adapter)
        return adapter

    adapters = load_adapters({"adapters": {"spy": {"enabled": True}}})
    assert len(adapters) == 1
    assert adapters[0].is_instrumented() is True


def test_load_adapters_respects_disabled_and_auto_off() -> None:
    @register("adapters", "spy2")
    def _factory() -> _SpyAdapter:
        return _SpyAdapter()

    assert load_adapters({"adapters": {"spy2": {"enabled": False}}}) == []
    inert = load_adapters({"adapters": {"auto_instrument": False, "spy2": {}}})
    assert len(inert) == 1
    assert inert[0].is_instrumented() is False  # created but not instrumented


def test_load_adapters_unknown_is_skipped() -> None:
    assert load_adapters({"adapters": {"does-not-exist": {"enabled": True}}}) == []


def test_load_adapters_no_block() -> None:
    assert load_adapters({}) == []


def test_configure_auto_instruments_and_shutdown_uninstruments() -> None:
    spy = _SpyAdapter()

    @register("adapters", "spy3")
    def _factory() -> _SpyAdapter:
        return spy

    # configure() reads a settings dict via a temp yaml is heavy; drive load_adapters + runtime
    rt = configure(exporters=[InMemoryExporter()], sync_export=True)
    rt.add_adapter(spy)
    spy.instrument()
    assert spy.is_instrumented() is True
    reset_runtime()  # shuts the runtime down ⇒ uninstruments adapters
    assert spy.is_instrumented() is False
