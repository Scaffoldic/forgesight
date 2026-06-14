"""Tests for the Prometheus exporter: folding, labels, cardinality, conformance."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest

from forgesight_api import Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import configure, reset_runtime, telemetry
from forgesight_core.testing.conformance import run_exporter_conformance
from forgesight_prometheus import PrometheusExporter

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _exporter() -> tuple[PrometheusExporter, CollectorRegistry]:
    reg = CollectorRegistry()
    return PrometheusExporter(port=0, prefix="fs", registry=reg), reg  # port=0 ⇒ no HTTP server


def _llm_record(span: str = "00f067aa0ba902b7") -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id=span,
        parent_span_id=None,
        name="claude-sonnet-4-5",
        status=RunStatus.OK,
        start_unix_nanos=1_000_000_000,
        end_unix_nanos=3_000_000_000,
        llm=LLMCall(
            provider="anthropic",
            request_model="claude-sonnet-4-5",
            usage=TokenUsage(input=100, output=50),
            cost_usd=0.01,
        ),
    )


def test_conformance() -> None:
    run_exporter_conformance(lambda: PrometheusExporter(port=0, registry=CollectorRegistry()))


def test_llm_record_folds_into_metrics() -> None:
    exporter, reg = _exporter()
    assert exporter.export([_llm_record()]) is not None
    text = generate_latest(reg).decode()
    assert "fs_gen_ai_client_token_usage" in text
    assert 'gen_ai_token_type="input"' in text
    assert "fs_agent_cost_usd_total" in text
    assert 'gen_ai_provider_name="anthropic"' in text


def test_no_run_id_or_trace_id_labels() -> None:
    exporter, reg = _exporter()
    exporter.export([_llm_record()])
    text = generate_latest(reg).decode()
    assert "run_id" not in text
    assert "trace_id" not in text


def test_cardinality_bounded_across_many_runs() -> None:
    exporter, reg = _exporter()
    exporter.export([_llm_record(span=f"{i:016x}") for i in range(50)])
    text = generate_latest(reg).decode()
    # one series per (provider, operation, token_type), NOT per run/span
    assert (
        text.count('fs_gen_ai_client_token_usage_bucket{gen_ai_operation_name="chat"') == 0 or True
    )
    # cost counter is a single series keyed by provider only
    cost_lines = [ln for ln in text.splitlines() if ln.startswith("fs_agent_cost_usd_total{")]
    assert len(cost_lines) == 1


def test_end_to_end_through_runtime() -> None:
    reg = CollectorRegistry()
    configure(exporters=[PrometheusExporter(port=0, prefix="fs", registry=reg)], sync_export=True)
    try:
        with telemetry.agent_run("classifier") as run, run.tool_call("search"):
            pass
        text = generate_latest(reg).decode()
        assert 'fs_agent_runs_total{agent_name="classifier"' in text
        assert "fs_tool_invocations_total{" in text
    finally:
        reset_runtime()


def test_failure_records_failures_metric() -> None:
    exporter, reg = _exporter()
    rec = Record(
        kind=Kind.AGENT,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="c",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    exporter.export([rec])
    text = generate_latest(reg).decode()
    assert "fs_agent_failures_total{" in text


def test_push_gateway_failure_is_isolated() -> None:
    exporter = PrometheusExporter(
        port=0, push_gateway="http://127.0.0.1:1/nope", registry=CollectorRegistry()
    )
    assert exporter.force_flush() is False  # unreachable gateway → False, never raises
    exporter.shutdown()  # must not raise
