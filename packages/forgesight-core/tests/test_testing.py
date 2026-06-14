"""Meta-tests for the testing & conformance harness (feat-011)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from forgesight_api import ExportResult, LifecycleEvent, Record, TokenUsage
from forgesight_core import (
    ConsoleExporter,
    ContentCaptureGate,
    PIIRedactionInterceptor,
    TablePricingProvider,
    configure,
    reset_runtime,
    telemetry,
)
from forgesight_core.testing import (
    InMemoryExporter,
    assert_span_tree,
    find_span,
    find_spans,
    llm_call_factory,
    token_usage_factory,
    tool_call_factory,
)
from forgesight_core.testing.conformance import (
    run_event_listener_conformance,
    run_exporter_conformance,
    run_interceptor_conformance,
    run_pricing_conformance,
)


def test_assert_span_tree_and_find_span() -> None:
    sink = InMemoryExporter()
    configure(exporters=[sink], sync_export=True)
    try:
        with telemetry.agent_run("classifier", version="1.0.0") as run:
            run.set_metadata(team="platform")
            with run.step("react-1"), run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=120, output=30)
        assert_span_tree(
            sink,
            {
                "op": "invoke_agent",
                "name": "classifier",
                "attrs": {"team": "platform"},
                "children": [
                    {"op": "step", "name": "react-1", "children": [{"op": "chat"}]},
                ],
            },
        )
        chat = find_span(sink, op="chat")
        assert chat.record.llm is not None
        assert chat.record.llm.usage.input == 120
        assert chat.record.llm.cost_usd is not None  # priced by the default table
        assert len(find_spans(sink, op="chat")) == 1
    finally:
        reset_runtime()


def test_assert_span_tree_mismatch_raises() -> None:
    sink = InMemoryExporter()
    configure(exporters=[sink], sync_export=True)
    try:
        with telemetry.agent_run("classifier"):
            pass
        with pytest.raises(AssertionError):
            assert_span_tree(sink, {"op": "invoke_agent", "name": "WRONG"})
    finally:
        reset_runtime()


def test_find_span_zero_or_many_raises() -> None:
    sink = InMemoryExporter()
    configure(exporters=[sink], sync_export=True)
    try:
        with telemetry.agent_run("c") as run:
            with run.tool_call("a"):
                pass
            with run.tool_call("b"):
                pass
        with pytest.raises(AssertionError):
            find_span(sink, op="execute_tool")  # two matches
        with pytest.raises(AssertionError):
            find_span(sink, op="embeddings")  # zero matches
    finally:
        reset_runtime()


def test_factories() -> None:
    usage = token_usage_factory(input=10, output=5)
    assert usage.total == 15
    call = llm_call_factory(
        provider="anthropic", request_model="m", input=10, output=5, cost_usd=0.1
    )
    assert call.provider == "anthropic"
    assert call.usage.input == 10
    assert call.cost_usd == 0.1
    tool = tool_call_factory(name="search", tool_type="function")
    assert tool.name == "search"


# --- conformance suites pass for shipped implementations -------------------
def test_shipped_exporters_pass_conformance() -> None:
    run_exporter_conformance(InMemoryExporter)
    run_exporter_conformance(ConsoleExporter)


def test_shipped_interceptors_pass_conformance() -> None:
    run_interceptor_conformance(ContentCaptureGate)
    run_interceptor_conformance(PIIRedactionInterceptor)


def test_shipped_listener_passes_conformance() -> None:
    class _Recorder:
        def on_event(self, event: LifecycleEvent) -> None:
            return None

    run_event_listener_conformance(_Recorder)


def test_shipped_pricing_passes_conformance() -> None:
    run_pricing_conformance(TablePricingProvider.from_vendored)


# --- the suites catch known-bad implementations (the headline meta-test) ---
def test_exporter_conformance_catches_a_raising_exporter() -> None:
    class _Bad:
        def export(self, records: Sequence[Record]) -> ExportResult:
            raise RuntimeError("contract violation: raised out of export")

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    with pytest.raises(Exception):  # noqa: B017, PT011 - any failure is acceptable here
        run_exporter_conformance(_Bad)


def test_pricing_conformance_catches_raise_on_unknown() -> None:
    class _Bad:
        def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
            raise KeyError("contract violation: raised on unknown model")

    with pytest.raises(Exception):  # noqa: B017, PT011
        run_pricing_conformance(_Bad)


def test_pricing_conformance_catches_zero_for_unknown() -> None:
    class _Bad:
        def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
            return 0.0  # should be None for an unknown model

    with pytest.raises(AssertionError):
        run_pricing_conformance(_Bad)
