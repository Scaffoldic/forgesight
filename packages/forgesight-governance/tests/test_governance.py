"""Tests for governance: budgets, policy, kill-switch — enforcement, mapping, conformance."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType

import pytest

from forgesight_api import Content, Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import InMemoryExporter, configure, reset_runtime, telemetry
from forgesight_core.testing.conformance import run_interceptor_conformance
from forgesight_governance import (
    BudgetCap,
    BudgetExceeded,
    BudgetInterceptor,
    BudgetScope,
    EnvKillSwitchSource,
    FileKillSwitchSource,
    KillSwitch,
    KillSwitchEngaged,
    PolicyAction,
    PolicyDenied,
    PolicyInterceptor,
    PolicyRule,
)

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _sink(*interceptors: object) -> InMemoryExporter:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], interceptors=list(interceptors), sync_export=True)
    return exporter


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_runtime()


def _llm_record(*, cost: float | None = 0.01, model: str = "m", **attrs: object) -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name=model,
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType(dict(attrs)),
        llm=LLMCall(
            provider="anthropic",
            request_model=model,
            usage=TokenUsage(input=100, output=50),
            cost_usd=cost,
        ),
    )


# --- budget: direct -----------------------------------------------------------
def test_budget_passes_within_cap() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=1.0)])
    assert b.intercept(_llm_record(cost=0.5)) is not None  # under cap ⇒ pass


def test_budget_non_llm_record_passes() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.001)])
    step = Record(
        kind=Kind.STEP,
        run_id="r",
        trace_id=TRACE,
        span_id="s",
        parent_span_id=None,
        name="x",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    assert b.intercept(step) is step  # governance ignores non-LLM records


def test_budget_raises_when_cap_crossed() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.05)])
    b.intercept(_llm_record(cost=0.03))  # total 0.03 — ok
    with pytest.raises(BudgetExceeded) as exc:
        b.intercept(_llm_record(cost=0.03))  # total 0.06 > 0.05 ⇒ trip
    assert exc.value.run_status is RunStatus.BUDGET_EXCEEDED
    assert exc.value.projected_usd == pytest.approx(0.06)


def test_budget_boundary_exactly_at_cap_is_ok() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.10)])
    assert b.intercept(_llm_record(cost=0.10)) is not None  # == cap is not > cap


def test_budget_token_cap() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, tokens=250)])
    b.intercept(_llm_record())  # 150 tokens accrued — under 250
    with pytest.raises(BudgetExceeded):
        b.intercept(_llm_record())  # 300 total > 250 ⇒ trip on tokens


def test_budget_per_team_keyed_on_metadata() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.TEAM, key="growth", usd=0.05)])
    b.intercept(_llm_record(cost=0.03, team="growth"))
    b.intercept(_llm_record(cost=0.10, team="research"))  # different team ⇒ not counted
    with pytest.raises(BudgetExceeded):
        b.intercept(_llm_record(cost=0.03, team="growth"))  # growth total 0.06 > 0.05


def test_budget_on_breach_drop() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.01)], on_breach="drop")
    assert b.intercept(_llm_record(cost=0.5)) is None  # dropped, not raised


def test_budget_on_breach_mark() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.01)], on_breach="mark")
    out = b.intercept(_llm_record(cost=0.5))
    assert out is not None
    assert out.attributes["forgesight.budget.exceeded"] is True


def test_budget_validation() -> None:
    with pytest.raises(ValueError, match="neither usd nor tokens"):
        BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN)])
    with pytest.raises(ValueError, match="on_breach must be"):
        BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=1.0)], on_breach="explode")


def test_budget_unpriced_model_skips_usd() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.01)])
    assert b.intercept(_llm_record(cost=None)) is not None  # cost None ⇒ no USD accrued


def test_budget_from_config() -> None:
    settings = {
        "governance": {
            "budgets": {
                "per_run": {"usd": 5.0},
                "per_team": {"growth": {"usd": 200.0}},
                "on_breach": "drop",
            }
        }
    }
    b = BudgetInterceptor.from_config(settings)
    assert b._on_breach == "drop"
    assert {c.scope for c in b._caps} == {BudgetScope.RUN, BudgetScope.TEAM}


# --- budget: end-to-end through the runtime ----------------------------------
def test_budget_trips_run_and_still_flushes() -> None:
    sink = _sink(BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=0.01)]))
    with (
        pytest.raises(BudgetExceeded),
        telemetry.agent_run("runaway") as run,
        run.llm_call("anthropic", "m") as call,
    ):
        call.set_cost(0.50)  # one over-budget call
    runs = [r for r in sink.records if r.kind is Kind.AGENT]
    assert len(runs) == 1
    assert runs[0].status is RunStatus.BUDGET_EXCEEDED  # run halted with the right terminal status
    _ = run


# --- policy -------------------------------------------------------------------
def test_policy_deny_model_in_prod() -> None:
    p = PolicyInterceptor(
        rules=[
            PolicyRule(
                match={"environment": "prod"}, action=PolicyAction.DENY, models=("*-preview",)
            ),
        ]
    )
    with pytest.raises(PolicyDenied) as exc:
        p.intercept(_llm_record(model="gpt-4-preview", environment="prod"))
    assert exc.value.run_status is RunStatus.GUARDRAIL


def test_policy_deny_but_model_not_in_set_passes() -> None:
    p = PolicyInterceptor(
        rules=[
            PolicyRule(
                match={"environment": "prod"}, action=PolicyAction.DENY, models=("*-preview",)
            ),
        ]
    )
    assert p.intercept(_llm_record(model="claude-sonnet-4-5", environment="prod")) is not None


def test_policy_allow_list_violation() -> None:
    p = PolicyInterceptor(
        rules=[
            PolicyRule(
                match={"team": "growth"}, action=PolicyAction.ALLOW, models=("claude-haiku-*",)
            ),
        ]
    )
    with pytest.raises(PolicyDenied):
        p.intercept(_llm_record(model="claude-opus-4", team="growth"))  # not in allow-list
    assert p.intercept(_llm_record(model="claude-haiku-3", team="growth")) is not None


def test_policy_redact_strips_content() -> None:
    p = PolicyInterceptor(rules=[PolicyRule(match={"pii": "true"}, action=PolicyAction.REDACT)])
    record = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id=TRACE,
        span_id="s",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType({"pii": "true", "gen_ai.input.messages": "secret"}),
        llm=LLMCall(provider="p", request_model="m", content=Content(input_messages=["hi"])),
    )
    out = p.intercept(record)
    assert out is not None
    assert "gen_ai.input.messages" not in out.attributes
    assert out.llm is not None
    assert out.llm.content is None


def test_policy_first_match_wins() -> None:
    p = PolicyInterceptor(
        rules=[
            PolicyRule(match={"team": "growth"}, action=PolicyAction.REDACT),
            PolicyRule(match={"team": "growth"}, action=PolicyAction.DENY, models=("*",)),
        ]
    )
    # first rule (redact) wins ⇒ no denial
    assert p.intercept(_llm_record(team="growth")) is not None


def test_policy_no_match_passes() -> None:
    p = PolicyInterceptor(
        rules=[PolicyRule(match={"team": "x"}, action=PolicyAction.DENY, models=("*",))]
    )
    assert p.intercept(_llm_record(team="growth")) is not None


def test_policy_validation() -> None:
    with pytest.raises(ValueError, match="must set models"):
        PolicyInterceptor(rules=[PolicyRule(match={}, action=PolicyAction.DENY)])


def test_policy_from_config() -> None:
    settings = {
        "governance": {
            "policies": {
                "rules": [
                    {"match": {"environment": "prod"}, "action": "deny", "models": ["*-preview"]},
                    {"match": {"pii": "true"}, "action": "redact"},
                ]
            }
        }
    }
    p = PolicyInterceptor.from_config(settings)
    assert len(p._rules) == 2


def test_policy_end_to_end_guardrail() -> None:
    sink = _sink(
        PolicyInterceptor(
            rules=[
                PolicyRule(
                    match={"environment": "prod"}, action=PolicyAction.DENY, models=("*-preview",)
                ),
            ]
        )
    )
    with (
        pytest.raises(PolicyDenied),
        telemetry.agent_run("r", metadata={"environment": "prod"}) as run,
        run.llm_call("openai", "gpt-4-preview"),
    ):
        pass
    runs = [r for r in sink.records if r.kind is Kind.AGENT]
    assert runs[0].status is RunStatus.GUARDRAIL


# --- kill switch --------------------------------------------------------------
def test_kill_switch_env_tripped() -> None:
    source = EnvKillSwitchSource(env={"FORGESIGHT_KILL_REPO_PAYMENTS_AGENT": "true"})
    ks = KillSwitch(source=source)
    with pytest.raises(KillSwitchEngaged) as exc:
        ks.intercept(_llm_record(repo="payments-agent"))
    assert exc.value.run_status is RunStatus.BUDGET_EXCEEDED
    assert exc.value.scope == "repo"


def test_kill_switch_not_tripped_passes() -> None:
    ks = KillSwitch(source=EnvKillSwitchSource(env={}))
    assert ks.intercept(_llm_record(repo="payments-agent")) is not None


def test_kill_switch_isolates_one_repo() -> None:
    source = EnvKillSwitchSource(env={"FORGESIGHT_KILL_REPO_PAYMENTS_AGENT": "true"})
    ks = KillSwitch(source=source)
    with pytest.raises(KillSwitchEngaged):
        ks.intercept(_llm_record(repo="payments-agent"))
    assert ks.intercept(_llm_record(repo="summariser")) is not None  # sibling repo keeps running


def test_kill_switch_file_source(tmp_path: Path) -> None:
    trip = tmp_path / "kill.txt"
    trip.write_text("# trips\nteam:research\n")
    source = FileKillSwitchSource(str(trip), poll_seconds=0)  # always re-read
    ks = KillSwitch(source=source)
    with pytest.raises(KillSwitchEngaged):
        ks.intercept(_llm_record(team="research"))
    trip.write_text("")  # clear the trip list
    assert ks.intercept(_llm_record(team="research")) is not None


def test_kill_switch_file_missing_is_fail_open(tmp_path: Path) -> None:
    source = FileKillSwitchSource(str(tmp_path / "nope.txt"), poll_seconds=0)
    assert KillSwitch(source=source).intercept(_llm_record(team="x")) is not None


def test_kill_switch_file_ttl_caches(tmp_path: Path) -> None:
    trip = tmp_path / "kill.txt"
    trip.write_text("team:research\n")
    clock = iter([0.0, 1.0, 2.0])  # within poll window ⇒ no re-read
    source = FileKillSwitchSource(str(trip), poll_seconds=100, clock=lambda: next(clock))
    assert source.is_tripped("team", "research") is True
    trip.write_text("")  # changed, but TTL not elapsed
    assert source.is_tripped("team", "research") is True  # served from cache


def test_kill_switch_from_config_file_requires_path() -> None:
    with pytest.raises(ValueError, match="requires file_path"):
        KillSwitch.from_config({"governance": {"kill_switch": {"source": "file"}}})


def test_kill_switch_from_config_env_default() -> None:
    ks = KillSwitch.from_config({})
    assert isinstance(ks._source, EnvKillSwitchSource)


def test_kill_switch_end_to_end() -> None:
    sink = _sink(KillSwitch(source=EnvKillSwitchSource(env={"FORGESIGHT_KILL_TEAM_GROWTH": "1"})))
    with (
        pytest.raises(KillSwitchEngaged),
        telemetry.agent_run("r", metadata={"team": "growth"}) as run,
        run.llm_call("openai", "m"),
    ):
        pass
    runs = [r for r in sink.records if r.kind is Kind.AGENT]
    assert runs[0].status is RunStatus.BUDGET_EXCEEDED


# --- conformance --------------------------------------------------------------
def test_interceptor_conformance() -> None:
    run_interceptor_conformance(
        lambda: BudgetInterceptor(caps=[BudgetCap(BudgetScope.RUN, usd=1.0)])
    )
    run_interceptor_conformance(
        lambda: PolicyInterceptor(rules=[PolicyRule(match={"x": "y"}, action=PolicyAction.REDACT)])
    )
    run_interceptor_conformance(lambda: KillSwitch(source=EnvKillSwitchSource(env={})))


def test_budget_per_team_absent_metadata_skips() -> None:
    b = BudgetInterceptor(caps=[BudgetCap(BudgetScope.TEAM, key="growth", usd=0.001)])
    assert b.intercept(_llm_record(cost=0.5)) is not None  # no team metadata ⇒ cap doesn't apply


def test_budget_from_config_empty() -> None:
    b = BudgetInterceptor.from_config({})  # no governance block ⇒ no caps
    assert b._caps == []


def test_kill_switch_from_config_file(tmp_path: Path) -> None:
    trip = tmp_path / "k.txt"
    trip.write_text("team:research\n")
    ks = KillSwitch.from_config(
        {
            "governance": {
                "kill_switch": {"source": "file", "file_path": str(trip), "poll_seconds": 0}
            }
        }
    )
    assert isinstance(ks._source, FileKillSwitchSource)
    with pytest.raises(KillSwitchEngaged):
        ks.intercept(_llm_record(team="research"))


def test_policy_from_config_ignores_malformed_rules() -> None:
    settings = {
        "governance": {
            "policies": {
                "rules": [
                    "not-a-dict",
                    {"action": "deny"},  # missing match
                    {"match": {"team": "x"}},  # missing action
                    {"match": {"pii": "true"}, "action": "redact"},  # the only valid one
                ]
            }
        }
    }
    p = PolicyInterceptor.from_config(settings)
    assert len(p._rules) == 1


def test_policy_from_config_non_list_rules() -> None:
    p = PolicyInterceptor.from_config({"governance": {"policies": {"rules": "nope"}}})
    assert p._rules == []
