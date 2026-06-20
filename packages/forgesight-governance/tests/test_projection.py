"""feat-026: pre-call budget projection + the budget-utilization metric."""

from __future__ import annotations

from typing import Any

import pytest

from forgesight_api import Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import configure, get_runtime, reset_runtime, telemetry
from forgesight_governance import (
    BudgetCap,
    BudgetExceeded,
    BudgetInterceptor,
    BudgetScope,
    ProjectionConfig,
)


class _FixedPricing:
    def __init__(self, cost: float | None) -> None:
        self._cost = cost

    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        return self._cost


class _SumPricing:
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        return float(usage.total)


def _llm_record(
    *,
    input: int = 0,
    output: int = 0,
    team: str | None = None,
    cost: float | None = None,
    run_id: str = "r1",
) -> Record:
    attrs: dict[str, object] = {"team": team} if team else {}
    return Record(
        kind=Kind.LLM,
        run_id=run_id,
        trace_id="t",
        span_id="s",
        parent_span_id=None,
        name="m",
        status=RunStatus.RUNNING if cost is None else RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=None if cost is None else 2,
        attributes=attrs,
        llm=LLMCall(
            provider="anthropic",
            request_model="m",
            usage=TokenUsage(input=input, output=output),
            cost_usd=cost,
        ),
    )


# --- precall projection (unit) ------------------------------------------------
def test_precall_denies_when_projected_over_cap() -> None:
    interceptor = BudgetInterceptor(
        caps=[BudgetCap(BudgetScope.TEAM, "growth", usd=100.0)],
        pricing=_FixedPricing(150.0),
        projection=ProjectionConfig(enabled=True),
    )
    with pytest.raises(BudgetExceeded):
        interceptor.precall(_llm_record(input=1000, output=8000, team="growth"))


def test_precall_allows_under_cap_and_does_not_commit() -> None:
    interceptor = BudgetInterceptor(
        caps=[BudgetCap(BudgetScope.TEAM, "growth", usd=100.0)],
        pricing=_FixedPricing(50.0),
        projection=ProjectionConfig(enabled=True),
    )
    interceptor.precall(_llm_record(input=1000, output=8000, team="growth"))  # no raise
    assert interceptor._totals == {}  # guard only — nothing committed
    # the running total advances only from the ACTUAL cost on the completed record
    interceptor.intercept(_llm_record(input=10, output=5, team="growth", cost=10.0))
    assert interceptor._totals[(BudgetScope.TEAM, "growth")]["usd"] == pytest.approx(10.0)


def test_precall_noop_when_projection_disabled() -> None:
    interceptor = BudgetInterceptor(
        caps=[BudgetCap(BudgetScope.TEAM, "growth", usd=1.0)],
        pricing=_FixedPricing(999.0),  # would breach, but projection is off
    )
    interceptor.precall(_llm_record(input=1, output=1, team="growth"))  # must not raise


def test_project_cost_estimate_modes() -> None:
    record = _llm_record(input=100, output=8000, team="growth")  # output carries max_tokens
    caps = [BudgetCap(BudgetScope.TEAM, "growth", usd=1e9)]
    max_mode = BudgetInterceptor(
        caps=caps, pricing=_SumPricing(), projection=ProjectionConfig(enabled=True)
    )
    assert max_mode._project_cost(record) == pytest.approx(100 + 8000)
    fixed = BudgetInterceptor(
        caps=caps,
        pricing=_SumPricing(),
        projection=ProjectionConfig(
            enabled=True, output_token_estimate="fixed", fixed_output_tokens=200
        ),
    )
    assert fixed._project_cost(record) == pytest.approx(100 + 200)
    ratio = BudgetInterceptor(
        caps=caps,
        pricing=_SumPricing(),
        projection=ProjectionConfig(
            enabled=True, output_token_estimate="input_ratio", input_ratio=2.0
        ),
    )
    assert ratio._project_cost(record) == pytest.approx(100 + 200)


def test_on_unpriced_allow_and_deny() -> None:
    record = _llm_record(input=1, output=1, team="growth")
    caps = [BudgetCap(BudgetScope.TEAM, "growth", usd=100.0)]
    deny = BudgetInterceptor(
        caps=caps,
        pricing=_FixedPricing(None),
        projection=ProjectionConfig(enabled=True, on_unpriced="deny"),
    )
    with pytest.raises(BudgetExceeded):
        deny.precall(record)
    allow = BudgetInterceptor(
        caps=caps,
        pricing=_FixedPricing(None),
        projection=ProjectionConfig(enabled=True, on_unpriced="allow"),
    )
    allow.precall(record)  # must not raise


def test_projection_config_validation() -> None:
    with pytest.raises(ValueError, match="output_token_estimate"):
        ProjectionConfig(output_token_estimate="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="on_unpriced"):
        ProjectionConfig(on_unpriced="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="fixed_output_tokens"):
        ProjectionConfig(output_token_estimate="fixed", fixed_output_tokens=0)
    with pytest.raises(ValueError, match="input_ratio"):
        ProjectionConfig(output_token_estimate="input_ratio", input_ratio=0)


def test_from_config_parses_projection() -> None:
    interceptor = BudgetInterceptor.from_config(
        {
            "governance": {
                "budgets": {
                    "per_team": {"growth": {"usd": 100}},
                    "projection": {
                        "enabled": True,
                        "output_token_estimate": "fixed",
                        "fixed_output_tokens": 50,
                    },
                }
            }
        }
    )
    assert interceptor._projection is not None
    assert interceptor._projection.enabled
    assert interceptor._projection.output_token_estimate == "fixed"


# --- integration (under a real runtime) ---------------------------------------
def test_projection_denies_before_the_call_is_made() -> None:
    configure(
        sync_export=True,
        interceptors=[
            BudgetInterceptor(
                caps=[BudgetCap(BudgetScope.TEAM, "research", usd=100.0)],
                pricing=_FixedPricing(150.0),
                projection=ProjectionConfig(enabled=True),
            )
        ],
    )
    body_ran: list[bool] = []
    try:
        with (
            pytest.raises(BudgetExceeded),
            telemetry.agent_run("etl", metadata={"team": "research"}) as run,
            run.llm_call(
                "anthropic",
                "claude-opus-4",
                projected_tokens={"input": 50_000, "max_tokens": 8_000},
            ),
        ):
            body_ran.append(True)  # the provider call must never happen
        assert body_ran == []
    finally:
        reset_runtime()


def test_projection_off_runs_normally() -> None:
    configure(
        sync_export=True,
        interceptors=[
            BudgetInterceptor(
                caps=[BudgetCap(BudgetScope.TEAM, "research", usd=100.0)],
                pricing=_FixedPricing(150.0),  # would breach IF projection were on
            )
        ],
    )
    body_ran: list[bool] = []
    try:
        with (
            telemetry.agent_run("etl", metadata={"team": "research"}) as run,
            run.llm_call("anthropic", "m", projected_tokens={"input": 50_000, "max_tokens": 8_000}),
        ):
            body_ran.append(True)
        assert body_ran == [True]  # projection off → call proceeds (feat-020 post-hoc only)
    finally:
        reset_runtime()


def test_budget_utilization_metric_emitted_on_intercept() -> None:
    configure(
        sync_export=True,
        interceptors=[BudgetInterceptor(caps=[BudgetCap(BudgetScope.TEAM, "growth", usd=1.0)])],
    )
    try:
        with (
            telemetry.agent_run("a", metadata={"team": "growth"}) as run,
            run.llm_call("anthropic", "m") as call,
        ):
            call.set_cost(0.5)
        data = get_runtime().metrics.collect()  # type: ignore[union-attr]
        points: list[Any] = []
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if metric.name == "forgesight.cost.budget_utilization":
                        points.extend(metric.data.data_points)
        assert any(
            p.value == pytest.approx(0.5) and p.attributes.get("budget.key") == "growth"
            for p in points
        )
    finally:
        reset_runtime()
