"""Tests for the metric instruments and their derivation from records."""

from __future__ import annotations

from typing import Any

import pytest

from forgesight_core import (
    MetricConfig,
    MetricsSubsystem,
    configure,
    get_runtime,
    reset_runtime,
    telemetry,
)
from forgesight_core.metrics.instruments import DURATION_BUCKETS, TOKEN_BUCKETS


def _metrics_by_name() -> dict[str, Any]:
    data = get_runtime().metrics.collect()  # type: ignore[union-attr]
    out: dict[str, Any] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = metric
    return out


def _points(metric: Any) -> list[Any]:
    return list(metric.data.data_points)


def test_run_with_llm_and_tool_derives_metrics() -> None:
    configure(sync_export=True)
    try:
        with telemetry.agent_run("classifier", version="1.2.0") as run:
            with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=100, output=50)
                call.set_cost(0.01)
            with run.tool_call("web_search"):
                pass
        metrics = _metrics_by_name()
        runs = _points(metrics["forgesight.agent.runs_total"])
        assert runs[0].value == 1
        assert runs[0].attributes["agent.name"] == "classifier"
        assert runs[0].attributes["agent.version"] == "1.2.0"
        assert _points(metrics["forgesight.tool.invocations_total"])[0].value == 1
        cost = _points(metrics["forgesight.agent.cost_total"])[0]
        assert cost.value == pytest.approx(0.01)
        assert cost.attributes["gen_ai.provider.name"] == "anthropic"
    finally:
        reset_runtime()


def test_token_usage_is_one_instrument_filtered_by_type_with_spec_buckets() -> None:
    configure(sync_export=True)
    try:
        with telemetry.agent_run("c") as run, run.llm_call("anthropic", "m") as call:
            call.record_usage(input=100, output=50, cache_read=10)
        usage = _metrics_by_name()["gen_ai.client.token.usage"]
        points = _points(usage)
        types = {p.attributes["gen_ai.token.type"] for p in points}
        assert types == {"input", "output", "cache_read"}
        assert tuple(points[0].explicit_bounds) == tuple(float(b) for b in TOKEN_BUCKETS)
        for p in points:
            assert p.attributes["gen_ai.provider.name"] == "anthropic"
            assert p.attributes["gen_ai.operation.name"] == "chat"
    finally:
        reset_runtime()


def test_operation_duration_buckets() -> None:
    configure(sync_export=True)
    try:
        with telemetry.agent_run("c") as run, run.llm_call("anthropic", "m"):
            pass
        dur = _metrics_by_name()["gen_ai.client.operation.duration"]
        assert tuple(_points(dur)[0].explicit_bounds) == tuple(float(b) for b in DURATION_BUCKETS)
    finally:
        reset_runtime()


def test_failure_increments_failures_total() -> None:
    configure(sync_export=True)
    try:
        with pytest.raises(ValueError, match="boom"), telemetry.agent_run("c"):
            raise ValueError("boom")
        failures = _points(_metrics_by_name()["forgesight.agent.failures_total"])
        assert failures[0].value == 1
        assert failures[0].attributes["error.type"] == "error"
    finally:
        reset_runtime()


def test_workflow_and_mcp_durations() -> None:
    configure(sync_export=True)
    try:
        with telemetry.workflow_run("nightly") as wf, wf.mcp_call("files", "tools/call", tool="rd"):
            pass
        metrics = _metrics_by_name()
        assert "gen_ai.workflow.duration" in metrics
        assert _points(metrics["forgesight.mcp.invocations_total"])[0].value == 1
        assert "mcp.client.operation.duration" in metrics
    finally:
        reset_runtime()


def test_enabled_instruments_subset() -> None:
    configure(
        sync_export=True,
        metrics=MetricConfig(enabled_instruments=frozenset({"forgesight.agent.runs_total"})),
    )
    try:
        with telemetry.agent_run("c") as run, run.llm_call("anthropic", "m"):
            pass
        metrics = _metrics_by_name()
        assert "forgesight.agent.runs_total" in metrics
        assert "gen_ai.client.token.usage" not in metrics
    finally:
        reset_runtime()


def test_unknown_instrument_fails_fast() -> None:
    with pytest.raises(ValueError, match="unknown instruments"):
        MetricsSubsystem(MetricConfig(enabled_instruments=frozenset({"bogus.metric"})))


def test_metric_config_validation() -> None:
    with pytest.raises(ValueError, match="export_interval_millis"):
        MetricConfig(export_interval_millis=0)


def test_metrics_can_be_disabled() -> None:
    rt = configure(sync_export=True, metrics=MetricConfig(enabled=False))
    try:
        assert rt.metrics is None
    finally:
        reset_runtime()
