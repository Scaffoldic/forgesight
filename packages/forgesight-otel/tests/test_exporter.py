"""Tests for OTelExporter: ReadableSpan construction, fault isolation, e2e."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from opentelemetry.trace.status import StatusCode

from forgesight_api import ExportResult, Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import configure, reset_runtime, telemetry
from forgesight_otel import OTelExporter
from forgesight_otel.exporter import _http_traces_endpoint, _status

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _llm_record(span: str, parent: str | None) -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id=span,
        parent_span_id=parent,
        name="claude-sonnet-4-5",
        status=RunStatus.OK,
        start_unix_nanos=1_000_000,
        end_unix_nanos=3_000_000,
        llm=LLMCall(
            provider="anthropic",
            request_model="claude-sonnet-4-5",
            usage=TokenUsage(input=100, output=50),
            cost_usd=0.01,
        ),
    )


def test_export_builds_readable_spans_with_our_ids() -> None:
    sink = InMemorySpanExporter()
    exporter = OTelExporter(span_exporter=sink, service_name="t")
    result = exporter.export([_llm_record("00f067aa0ba902b7", "0011223344556677")])
    assert result is ExportResult.SUCCESS
    spans = sink.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "chat claude-sonnet-4-5"
    assert span.kind is SpanKind.CLIENT
    assert span.context is not None
    assert format(span.context.trace_id, "032x") == TRACE
    assert format(span.context.span_id, "016x") == "00f067aa0ba902b7"
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == "0011223344556677"
    assert span.attributes is not None
    assert span.attributes["gen_ai.provider.name"] == "anthropic"
    assert span.attributes["forgesight.usage.cost_usd"] == 0.01
    assert span.resource.attributes["forgesight.semconv_version"]


def test_export_returns_failure_when_sink_returns_failure() -> None:
    class FailSink(InMemorySpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:  # type: ignore[override]
            return SpanExportResult.FAILURE

    exporter = OTelExporter(span_exporter=FailSink())
    assert exporter.export([_llm_record("00f067aa0ba902b7", None)]) is ExportResult.FAILURE


def test_export_never_raises() -> None:
    class BoomSink(InMemorySpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:  # type: ignore[override]
            raise RuntimeError("otlp down")

    exporter = OTelExporter(span_exporter=BoomSink())
    assert exporter.export([_llm_record("00f067aa0ba902b7", None)]) is ExportResult.FAILURE


def test_error_record_sets_error_span_status() -> None:
    sink = InMemorySpanExporter()
    exporter = OTelExporter(span_exporter=sink)
    rec = Record(
        kind=Kind.AGENT,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="classifier",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    exporter.export([rec])
    span = sink.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR


def test_status_mapping() -> None:
    assert _status(RunStatus.OK).status_code is StatusCode.OK
    assert _status(RunStatus.RUNNING).status_code is StatusCode.UNSET
    assert _status(RunStatus.GUARDRAIL).status_code is StatusCode.ERROR


def test_builds_http_exporter_and_shuts_down() -> None:
    exporter = OTelExporter(protocol="http/protobuf", endpoint="http://localhost:4318")
    assert exporter.force_flush() is True
    exporter.shutdown()


def test_unknown_protocol_raises() -> None:
    with pytest.raises(ValueError, match="unknown protocol"):
        OTelExporter(protocol="carrier-pigeon")


def test_end_to_end_through_runtime() -> None:
    sink = InMemorySpanExporter()
    configure(exporters=[OTelExporter(span_exporter=sink)], sync_export=True)
    try:
        with telemetry.agent_run("issue-classifier", version="1.2.0") as run:
            run.set_metadata(team="platform")
            with run.step("react-1"), run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=10, output=5)
        names = sorted(s.name for s in sink.get_finished_spans())
        assert names == ["chat claude-sonnet-4-5", "invoke_agent issue-classifier", "react-1"]
        agent_span = next(s for s in sink.get_finished_spans() if s.name.startswith("invoke_agent"))
        assert agent_span.attributes is not None
        assert agent_span.attributes["team"] == "platform"
        assert agent_span.attributes["gen_ai.agent.version"] == "1.2.0"
    finally:
        reset_runtime()


def test_http_traces_endpoint_appends_signal_path() -> None:
    # a base URL gets /v1/traces appended (OTLP/HTTP does not append it when endpoint is set)
    assert _http_traces_endpoint("http://localhost:4318") == "http://localhost:4318/v1/traces"
    assert _http_traces_endpoint("http://localhost:4318/") == "http://localhost:4318/v1/traces"
    assert _http_traces_endpoint("https://otlp.example.com") == "https://otlp.example.com/v1/traces"
    # an explicit path (a custom collector route) is left untouched
    assert (
        _http_traces_endpoint("http://localhost:4318/v1/traces")
        == "http://localhost:4318/v1/traces"
    )
    assert _http_traces_endpoint("http://collector/custom/path") == "http://collector/custom/path"
    # None defers to the OTel env-var defaults
    assert _http_traces_endpoint(None) is None
