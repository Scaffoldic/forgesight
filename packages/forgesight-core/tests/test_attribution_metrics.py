"""feat-026: live attributed-cost metric + the budget-utilization gauge setter."""

from __future__ import annotations

from typing import Any

import pytest

from forgesight_core import (
    AttributionMetricsConfig,
    MetricConfig,
    MetricsSubsystem,
    configure,
    get_runtime,
    reset_runtime,
    telemetry,
)


def _by_name(data: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = metric
    return out


def _points(metric: Any) -> list[Any]:
    return list(metric.data.data_points)


def test_attributed_cost_metric_keyed_by_dimensions() -> None:
    configure(
        sync_export=True,
        metrics=MetricConfig(
            attribution=AttributionMetricsConfig(enabled=True, dimensions=("team", "owner"))
        ),
    )
    try:
        with (
            telemetry.agent_run("inv", metadata={"team": "fin", "owner": "o@x.com"}) as run,
            run.llm_call("anthropic", "claude-sonnet-4-5") as call,
        ):
            call.set_cost(0.02)
        metrics = _by_name(get_runtime().metrics.collect())  # type: ignore[union-attr]
        point = _points(metrics["forgesight.cost.attributed_usd"])[0]
        assert point.value == pytest.approx(0.02)
        assert point.attributes["team"] == "fin"
        assert point.attributes["owner"] == "o@x.com"
        assert point.attributes["gen_ai.provider.name"] == "anthropic"
        # provider-keyed cost_total is unchanged (feat-005)
        assert _points(metrics["forgesight.agent.cost_total"])[0].value == pytest.approx(0.02)
    finally:
        reset_runtime()


def test_missing_dimension_buckets_unattributed() -> None:
    configure(
        sync_export=True,
        metrics=MetricConfig(
            attribution=AttributionMetricsConfig(enabled=True, dimensions=("team",))
        ),
    )
    try:
        with telemetry.agent_run("inv") as run, run.llm_call("anthropic", "m") as call:  # no team
            call.set_cost(0.01)
        point = _points(
            _by_name(get_runtime().metrics.collect())["forgesight.cost.attributed_usd"]
        )[0]  # type: ignore[union-attr]
        assert point.attributes["team"] == "<unattributed>"
    finally:
        reset_runtime()


def test_attribution_disabled_emits_no_attributed_metric() -> None:
    configure(sync_export=True)  # attribution off by default
    try:
        with (
            telemetry.agent_run("inv", metadata={"team": "fin"}) as run,
            run.llm_call("anthropic", "m") as call,
        ):
            call.set_cost(0.01)
        metrics = _by_name(get_runtime().metrics.collect())  # type: ignore[union-attr]
        assert "forgesight.cost.attributed_usd" not in metrics or not _points(
            metrics["forgesight.cost.attributed_usd"]
        )
        assert "forgesight.agent.cost_total" in metrics  # the provider-keyed one still emits
    finally:
        reset_runtime()


def test_attribution_config_requires_dimensions_when_enabled() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        AttributionMetricsConfig(enabled=True, dimensions=())


def test_budget_utilization_gauge_is_settable() -> None:
    subsystem = MetricsSubsystem(MetricConfig())
    subsystem.set_budget_utilization(0.42, {"budget.scope": "team", "budget.key": "growth"})
    point = _points(_by_name(subsystem.collect())["forgesight.cost.budget_utilization"])[0]
    assert point.value == pytest.approx(0.42)
    assert point.attributes["budget.key"] == "growth"
