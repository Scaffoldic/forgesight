"""Tests for the Datadog exporter: mapping, unified tags, cost metric, transports."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from forgesight_api import (
    Content,
    ErrorInfo,
    ExportResult,
    Kind,
    LLMCall,
    MCPCall,
    Record,
    RunStatus,
    TokenUsage,
    ToolCall,
)
from forgesight_core import configure, reset_runtime, telemetry
from forgesight_core.testing.conformance import run_exporter_conformance
from forgesight_datadog import (
    COST_METRIC,
    OTLP_NATIVE_BACKENDS,
    TOKENS_METRIC,
    DatadogExporter,
    InMemoryDatadogMetricSink,
    InMemoryDatadogSpanWriter,
)
from forgesight_datadog.exporter import record_to_span

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _agent_exporter(
    **kw: object,
) -> tuple[DatadogExporter, InMemoryDatadogSpanWriter, InMemoryDatadogMetricSink]:
    writer = InMemoryDatadogSpanWriter()
    sink = InMemoryDatadogMetricSink()
    exporter = DatadogExporter(span_writer=writer, metric_sink=sink, **kw)
    return exporter, writer, sink


def _llm_record(span: str = "00f067aa0ba902b7") -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id=span,
        parent_span_id="aaaaaaaaaaaaaaaa",
        name="claude-sonnet-4-5",
        status=RunStatus.OK,
        start_unix_nanos=1_000_000_000,
        end_unix_nanos=3_000_000_000,
        llm=LLMCall(
            provider="anthropic",
            request_model="claude-sonnet-4-5",
            usage=TokenUsage(input=100, output=50, cache_read=10),
            cost_usd=0.01,
        ),
    )


# --- construction / validation ------------------------------------------------
def test_unknown_site_rejected() -> None:
    with pytest.raises(ValueError, match="unknown Datadog site"):
        DatadogExporter(span_writer=InMemoryDatadogSpanWriter(), site="datadoghq.invalid")


def test_unknown_transport_rejected() -> None:
    with pytest.raises(ValueError, match="transport must be"):
        DatadogExporter(span_writer=InMemoryDatadogSpanWriter(), transport="carrier-pigeon")


def test_otlp_requires_agent_endpoint() -> None:
    with pytest.raises(ValueError, match="requires agent_endpoint"):
        DatadogExporter(transport="otlp")


def test_agent_direct_intake_requires_api_key() -> None:
    with pytest.raises(ValueError, match="requires api_key"):
        DatadogExporter(transport="agent")  # no writer, no endpoint, no api_key


def test_api_key_satisfies_agent_intake() -> None:
    # api_key present ⇒ the default writer would be built; inject one to stay offline.
    exporter, _, _ = _agent_exporter(api_key="dd-key")
    assert isinstance(exporter, DatadogExporter)


# --- conformance --------------------------------------------------------------
def test_conformance() -> None:
    run_exporter_conformance(
        lambda: DatadogExporter(
            span_writer=InMemoryDatadogSpanWriter(), metric_sink=InMemoryDatadogMetricSink()
        )
    )


# --- record → DD span mapping -------------------------------------------------
def test_llm_span_mapping_and_unified_tags() -> None:
    exporter, writer, _ = _agent_exporter(service="classifier", env="prod", version="1.2.0")
    assert exporter.export([_llm_record()]) is ExportResult.SUCCESS
    [span] = writer.spans
    assert span.name == "forgesight.llm"
    assert span.resource == "claude-sonnet-4-5"
    assert span.service == "classifier"
    assert span.meta["env"] == "prod"
    assert span.meta["version"] == "1.2.0"
    assert span.meta["gen_ai.provider.name"] == "anthropic"
    assert span.meta["gen_ai.operation.name"] == "chat"
    assert span.meta["forgesight.run_id"] == "01J9Z3K7P8QF2R5V6W7X8Y9Z0A"
    assert span.metrics["gen_ai.usage.input_tokens"] == 100.0
    assert span.metrics["gen_ai.usage.cache_read_tokens"] == 10.0
    assert span.metrics[COST_METRIC] == 0.01
    assert span.meta[COST_METRIC] == "0.010000"  # cost is also a monitorable span tag
    assert span.error == 0
    assert span.duration_ns == 2_000_000_000


def test_cost_and_tokens_emitted_as_dd_metrics() -> None:
    exporter, _, metrics = _agent_exporter(service="classifier", env="prod")
    exporter.export([_llm_record()])
    [cost] = metrics.named(COST_METRIC)
    assert cost.value == 0.01
    assert "service:classifier" in cost.tags
    assert "env:prod" in cost.tags
    assert "provider:anthropic" in cost.tags
    token_types = {
        t.split(":", 1)[1]
        for m in metrics.named(TOKENS_METRIC)
        for t in m.tags
        if t.startswith("gen_ai_token_type:")
    }
    assert {"input", "output", "cache_read"} <= token_types


def test_tool_mcp_step_workflow_mapping() -> None:
    exporter, writer, _ = _agent_exporter()
    records = [
        Record(
            kind=Kind.WORKFLOW,
            run_id="r",
            trace_id=TRACE,
            span_id="1111111111111111",
            parent_span_id=None,
            name="nightly",
            status=RunStatus.OK,
            start_unix_nanos=1,
            end_unix_nanos=2,
        ),
        Record(
            kind=Kind.TOOL,
            run_id="r",
            trace_id=TRACE,
            span_id="2222222222222222",
            parent_span_id=None,
            name="search",
            status=RunStatus.OK,
            start_unix_nanos=1,
            end_unix_nanos=2,
            tool=ToolCall(name="search"),
        ),
        Record(
            kind=Kind.MCP,
            run_id="r",
            trace_id=TRACE,
            span_id="3333333333333333",
            parent_span_id=None,
            name="tools/call",
            status=RunStatus.OK,
            start_unix_nanos=1,
            end_unix_nanos=2,
            mcp=MCPCall(server="files", method="tools/call", tool="read_file"),
        ),
        Record(
            kind=Kind.STEP,
            run_id="r",
            trace_id=TRACE,
            span_id="4444444444444444",
            parent_span_id=None,
            name="react-1",
            status=RunStatus.OK,
            start_unix_nanos=1,
            end_unix_nanos=2,
        ),
    ]
    exporter.export(records)
    by_name = {s.name: s for s in writer.spans}
    assert by_name["forgesight.workflow"].resource == "nightly"
    assert by_name["forgesight.tool"].meta["gen_ai.tool.name"] == "search"
    assert by_name["forgesight.tool"].meta["gen_ai.operation.name"] == "execute_tool"
    mcp = by_name["forgesight.mcp"]
    assert mcp.resource == "tools/call"
    assert mcp.meta["mcp.server"] == "files"
    assert mcp.meta["gen_ai.tool.name"] == "read_file"
    assert "gen_ai.operation.name" not in by_name["forgesight.step"].meta  # step has no op


def test_error_record_maps_error_fields() -> None:
    exporter, writer, _ = _agent_exporter()
    rec = Record(
        kind=Kind.TOOL,
        run_id="r",
        trace_id=TRACE,
        span_id="5555555555555555",
        parent_span_id=None,
        name="search",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
        tool=ToolCall(name="search"),
        error=ErrorInfo(error_type="TimeoutError", message="boom"),
    )
    exporter.export([rec])
    [span] = writer.spans
    assert span.error == 1
    assert span.meta["error.type"] == "TimeoutError"
    assert span.meta["error.message"] == "boom"


def test_error_type_falls_back_to_status() -> None:
    span = record_to_span(
        Record(
            kind=Kind.AGENT,
            run_id="r",
            trace_id=TRACE,
            span_id="6666666666666666",
            parent_span_id=None,
            name="c",
            status=RunStatus.GUARDRAIL,
            start_unix_nanos=1,
            end_unix_nanos=2,
        ),
        service="s",
        env=None,
        version=None,
        capture_content=False,
    )
    assert span.meta["error.type"] == "guardrail"
    assert "env" not in span.meta  # unset unified tags are omitted
    assert span.error == 1


def test_open_span_has_zero_duration() -> None:
    span = record_to_span(
        Record(
            kind=Kind.AGENT,
            run_id="r",
            trace_id=TRACE,
            span_id="7777777777777777",
            parent_span_id=None,
            name="c",
            status=RunStatus.RUNNING,
            start_unix_nanos=5,
            end_unix_nanos=None,
        ),
        service="s",
        env=None,
        version=None,
        capture_content=False,
    )
    assert span.duration_ns == 0
    assert span.error == 0  # RUNNING is not an error


# --- content gating (P7) ------------------------------------------------------
def test_content_omitted_unless_capture_content() -> None:
    rec = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id=TRACE,
        span_id="8888888888888888",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        llm=LLMCall(
            provider="anthropic",
            request_model="m",
            content=Content(
                input_messages=[{"role": "user", "content": "hi"}],
                output_messages=[{"role": "assistant", "content": "yo"}],
                system_instructions="be terse",
            ),
        ),
    )
    off = record_to_span(rec, service="s", env=None, version=None, capture_content=False)
    assert "gen_ai.input.messages" not in off.meta
    on = record_to_span(rec, service="s", env=None, version=None, capture_content=True)
    assert json.loads(on.meta["gen_ai.input.messages"]) == [{"role": "user", "content": "hi"}]
    assert json.loads(on.meta["gen_ai.output.messages"]) == [{"role": "assistant", "content": "yo"}]
    assert on.meta["gen_ai.system_instructions"] == '"be terse"'


def test_response_model_and_missing_cost() -> None:
    exporter, writer, sink = _agent_exporter()
    rec = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id=TRACE,
        span_id="9999999999999999",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        llm=LLMCall(
            provider="anthropic",
            request_model="m",
            response_model="m-2099",
            usage=TokenUsage(input=3),
        ),  # no cost_usd ⇒ no cost metric, no cost span tag
    )
    exporter.export([rec])
    [span] = writer.spans
    assert span.meta["gen_ai.response.model"] == "m-2099"
    assert COST_METRIC not in span.metrics
    assert sink.named(COST_METRIC) == []  # cost metric only when the SDK priced the call


def test_structured_attrs_lift_to_meta() -> None:
    from types import MappingProxyType

    rec = Record(
        kind=Kind.AGENT,
        run_id="r",
        trace_id=TRACE,
        span_id="abababababababab",
        parent_span_id=None,
        name="inner",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType(
            {"parent.run_id": "p-1", "context.id": "conv-7", "agent.version": "3.0", "team": "x"}
        ),
    )
    span = record_to_span(rec, service="s", env=None, version=None, capture_content=False)
    assert span.meta["forgesight.parent_run_id"] == "p-1"
    assert span.meta["gen_ai.conversation.id"] == "conv-7"
    assert span.meta["gen_ai.agent.version"] == "3.0"
    assert span.meta["team"] == "x"  # business metadata passes through
    # structured keys are lifted, not left under their raw attribute names
    assert "parent.run_id" not in span.meta


def test_mcp_without_tool() -> None:
    span = record_to_span(
        Record(
            kind=Kind.MCP,
            run_id="r",
            trace_id=TRACE,
            span_id="cdcdcdcdcdcdcdcd",
            parent_span_id=None,
            name="resources/list",
            status=RunStatus.OK,
            start_unix_nanos=1,
            end_unix_nanos=2,
            mcp=MCPCall(server="files", method="resources/list"),
        ),
        service="s",
        env=None,
        version=None,
        capture_content=False,
    )
    assert span.meta["mcp.method.name"] == "resources/list"
    assert "gen_ai.tool.name" not in span.meta  # no tool on a non-tools/call method
    assert "gen_ai.operation.name" not in span.meta


def test_capture_content_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGESIGHT_CAPTURE_CONTENT", "true")
    exporter, writer, _ = _agent_exporter()
    rec = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id=TRACE,
        span_id="efefefefefefefef",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        llm=LLMCall(provider="p", request_model="m", content=Content(input_messages=["hi"])),
    )
    exporter.export([rec])
    assert "gen_ai.input.messages" in writer.spans[0].meta  # env-enabled capture
    assert writer.by_resource()["m"].name == "forgesight.llm"  # by_resource accessor


# --- denormalized metadata + structured fields --------------------------------
def test_business_metadata_and_structured_fields_on_span() -> None:
    exporter, writer, _ = _agent_exporter(service="classifier")
    configure(exporters=[exporter], sync_export=True)
    try:
        with telemetry.agent_run("classifier", version="9.9") as run:
            run.set_metadata(team="payments")
            with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=10, output=5)
    finally:
        reset_runtime()
    for span in writer.spans:
        assert span.meta["team"] == "payments"  # inherited onto the child llm span
    agent_span = next(s for s in writer.spans if s.name == "forgesight.agent")
    assert agent_span.meta["gen_ai.agent.name"] == "classifier"
    assert agent_span.meta["gen_ai.agent.version"] == "9.9"


# --- fault isolation (P6) -----------------------------------------------------
class _FailingWriter:
    def write(self, span: object) -> None:
        raise ConnectionError("dd agent unreachable")

    def flush(self) -> bool:
        return False

    def stop(self) -> None:
        pass


def test_agent_outage_is_isolated() -> None:
    exporter = DatadogExporter(
        span_writer=_FailingWriter(), metric_sink=InMemoryDatadogMetricSink()
    )
    assert exporter.export([_llm_record()]) is ExportResult.FAILURE  # counted, never raised


# --- lifecycle ----------------------------------------------------------------
def test_force_flush_and_shutdown_delegate() -> None:
    exporter, writer, sink = _agent_exporter()
    assert exporter.force_flush() is True
    assert writer.flushed == 1
    exporter.shutdown()
    assert writer.stopped is True
    assert sink.closed is True


# --- otlp transport -----------------------------------------------------------
def test_otlp_transport_applies_unified_tags() -> None:
    span_sink = InMemorySpanExporter()
    exporter = DatadogExporter(
        transport="otlp",
        agent_endpoint="http://datadog-agent:4317",
        service="classifier",
        env="prod",
        version="1.0.0",
        span_exporter=span_sink,
    )
    configure(exporters=[exporter], sync_export=True)
    try:
        with telemetry.agent_run("classifier"):
            pass
    finally:
        reset_runtime()
    spans = span_sink.get_finished_spans()
    assert spans
    resource = spans[0].resource.attributes
    assert resource["service.name"] == "classifier"
    assert resource["deployment.environment"] == "prod"
    assert resource["service.version"] == "1.0.0"


def test_otlp_transport_exports_records() -> None:
    span_sink = InMemorySpanExporter()
    exporter = DatadogExporter(
        transport="otlp", agent_endpoint="http://dd:4318", span_exporter=span_sink
    )
    assert exporter.export([_llm_record()]) is ExportResult.SUCCESS
    assert span_sink.get_finished_spans()


def test_otlp_transport_flush_and_shutdown_delegate() -> None:
    exporter = DatadogExporter(
        transport="otlp", agent_endpoint="http://dd:4318", span_exporter=InMemorySpanExporter()
    )
    assert exporter.force_flush() is True
    exporter.shutdown()  # must not raise


# --- config: env resolution ---------------------------------------------------
def test_env_resolves_and_kwargs_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_SERVICE", "from-env")
    monkeypatch.setenv("DD_ENV", "staging")
    monkeypatch.setenv("FORGESIGHT_DATADOG_TRANSPORT", "agent")
    from_env, _, _ = _agent_exporter()
    assert from_env._service == "from-env"
    assert from_env._env == "staging"
    explicit, _, _ = _agent_exporter(service="explicit")
    assert explicit._service == "explicit"  # kwarg wins over DD_SERVICE


# --- keystone doc test --------------------------------------------------------
def test_otlp_native_backends_need_no_package() -> None:
    # The load-bearing note: these route through forgesight-otel, never a package.
    assert "honeycomb" in OTLP_NATIVE_BACKENDS
    assert "datadog" not in OTLP_NATIVE_BACKENDS  # the deliberate exception
    assert all("forgesight-otel" in how for how in OTLP_NATIVE_BACKENDS.values())


# --- resolves by entry-point name ---------------------------------------------
def test_resolves_by_name() -> None:
    from forgesight_core.config import resolve

    exporter = resolve(
        "exporters", "datadog", {"transport": "otlp", "agent_endpoint": "http://dd:4318"}
    )
    assert isinstance(exporter, DatadogExporter)


# --- vendor edge imports cleanly (the ddtrace-touching code is live-agent-only) ---
def test_ddtrace_edge_module_imports() -> None:
    from forgesight_datadog import _ddtrace

    assert _ddtrace._DD_64BIT_MASK == (1 << 64) - 1
    assert hasattr(_ddtrace, "DDTraceSpanWriter")
    assert hasattr(_ddtrace, "DogStatsdMetricSink")
