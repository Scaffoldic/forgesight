"""Tests for the registry: resolution, stamping, chargeback, catalogue, config."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from forgesight_api import Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import InMemoryExporter, configure, reset_runtime, telemetry
from forgesight_registry import (
    AgentCatalogue,
    AgentEntry,
    ChargebackReport,
    Lifecycle,
    Registry,
    RegistryUnmatched,
    install,
    installed_registry,
    reset_for_tests,
)

ENTRIES = [
    {
        "name": "invoice-parser",
        "version": "2.3.0",
        "owner": "fin@acme",
        "team": "finance",
        "repo": "acme/inv",
        "lifecycle": "ga",
        "sla_tier": "tier-1",
    },
    {
        "name": "summariser",
        "version": "*",
        "owner": "growth@acme",
        "team": "growth",
        "lifecycle": "beta",
    },
]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_runtime()
    reset_for_tests()


def _reg(**kw: object) -> Registry:
    return Registry.from_entries(ENTRIES, **kw)


# --- resolution ---------------------------------------------------------------
def test_resolve_exact_then_wildcard_then_none() -> None:
    reg = _reg()
    assert reg.resolve("invoice-parser", "2.3.0").team == "finance"  # type: ignore[union-attr]
    assert reg.resolve("invoice-parser", "9.9.9") is None  # exact miss, no wildcard
    assert reg.resolve("summariser", "1.0.0").team == "growth"  # type: ignore[union-attr]  # wildcard
    assert reg.resolve("unknown", "1.0") is None


def test_ownership_metadata_fields() -> None:
    reg = _reg()
    meta = reg.ownership_metadata("invoice-parser", "2.3.0")
    assert meta == {
        "owner": "fin@acme",
        "team": "finance",
        "repo": "acme/inv",
        "lifecycle": "ga",
        "sla_tier": "tier-1",
    }


def test_ownership_metadata_field_filter_and_prefix() -> None:
    reg = _reg(stamp_fields=["team", "owner"], prefix="org.")
    meta = reg.ownership_metadata("invoice-parser", "2.3.0")
    assert meta == {"org.team": "finance", "org.owner": "fin@acme"}


def test_extra_fields_stamped() -> None:
    reg = Registry.from_entries([{"name": "x", "version": "*", "team": "t", "cost_center": "cc-9"}])
    assert reg.ownership_metadata("x", "1")["cost_center"] == "cc-9"


# --- on_unmatched -------------------------------------------------------------
def test_on_unmatched_warn_counts(caplog: pytest.LogCaptureFixture) -> None:
    reg = _reg(on_unmatched="warn")
    with caplog.at_level("WARNING"):
        assert reg.ownership_metadata("ghost", "1.0") == {}
    assert reg.unmatched_count == 1
    assert any("undeclared agent" in r.message for r in caplog.records)


def test_on_unmatched_ignore_silent() -> None:
    reg = _reg(on_unmatched="ignore")
    assert reg.ownership_metadata("ghost", "1.0") == {}
    assert reg.unmatched_count == 1


def test_on_unmatched_error_raises() -> None:
    reg = _reg(on_unmatched="error")
    with pytest.raises(RegistryUnmatched):
        reg.ownership_metadata("ghost", "1.0")


def test_invalid_on_unmatched() -> None:
    with pytest.raises(ValueError, match="on_unmatched must be"):
        _reg(on_unmatched="explode")


# --- stamping at run start (core hook) ---------------------------------------
def test_stamps_ownership_on_run_and_children() -> None:
    reg = _reg()
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, run_metadata_provider=reg.ownership_metadata)
    with (
        telemetry.agent_run("invoice-parser", version="2.3.0") as run,
        run.llm_call("anthropic", "m"),
    ):
        pass
    agent = next(r for r in exporter.records if r.kind is Kind.AGENT)
    llm = next(r for r in exporter.records if r.kind is Kind.LLM)
    assert agent.attributes["team"] == "finance"
    assert agent.attributes["owner"] == "fin@acme"
    assert llm.attributes["team"] == "finance"  # propagated onto the child (FR-5)


def test_caller_metadata_wins_over_registry() -> None:
    reg = _reg()
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, run_metadata_provider=reg.ownership_metadata)
    with telemetry.agent_run("invoice-parser", version="2.3.0", metadata={"team": "override"}):
        pass
    agent = next(r for r in exporter.records if r.kind is Kind.AGENT)
    assert agent.attributes["team"] == "override"  # caller-set key wins


def test_unregistered_run_is_unstamped() -> None:
    reg = _reg(on_unmatched="ignore")
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, run_metadata_provider=reg.ownership_metadata)
    with telemetry.agent_run("ghost", version="9.9"):
        pass
    agent = next(r for r in exporter.records if r.kind is Kind.AGENT)
    assert "team" not in agent.attributes
    assert reg.unmatched_count == 1


# --- file source --------------------------------------------------------------
def test_from_file(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        "agents:\n"
        "  - name: a\n    version: '1.0'\n    team: t1\n    owner: o1\n"
        "  - name: b\n    team: t2\n"
    )
    reg = Registry.from_file(str(path))
    assert reg.resolve("a", "1.0").team == "t1"  # type: ignore[union-attr]
    assert reg.resolve("b", "anything").team == "t2"  # type: ignore[union-attr]  # default wildcard


# --- chargeback ---------------------------------------------------------------
def _agent(team: str, env: str, run_id: str, status: RunStatus = RunStatus.OK) -> Record:
    from types import MappingProxyType

    return Record(
        kind=Kind.AGENT,
        run_id=run_id,
        trace_id="t",
        span_id=run_id,
        parent_span_id=None,
        name="a",
        status=status,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType({"team": team, "environment": env}),
    )


def _llm(team: str, env: str, run_id: str, cost: float, tokens: int = 100) -> Record:
    from types import MappingProxyType

    return Record(
        kind=Kind.LLM,
        run_id=run_id,
        trace_id="t",
        span_id=f"{run_id}-l",
        parent_span_id=run_id,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType({"team": team, "environment": env}),
        llm=LLMCall(provider="p", request_model="m", usage=TokenUsage(input=tokens), cost_usd=cost),
    )


def test_chargeback_groups_and_totals() -> None:
    records = [
        _agent("growth", "prod", "r1"),
        _llm("growth", "prod", "r1", 0.10, 100),
        _agent("growth", "prod", "r2"),
        _llm("growth", "prod", "r2", 0.20, 200),
        _agent("research", "dev", "r3", RunStatus.ERROR),
        _llm("research", "dev", "r3", 0.05, 50),
    ]
    report = ChargebackReport.from_records(records, dimensions=["team", "environment"])
    rows = {(r.dimensions["team"], r.dimensions["environment"]): r for r in report.rows()}
    growth = rows[("growth", "prod")]
    assert growth.cost_usd == pytest.approx(0.30)
    assert growth.run_count == 2
    assert growth.token_total == 300
    assert growth.failure_count == 0
    assert rows[("research", "dev")].failure_count == 1
    assert report.total_usd() == pytest.approx(0.35)


def test_chargeback_unattributed_bucket() -> None:
    from types import MappingProxyType

    rec = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id="t",
        span_id="s",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType({}),  # no team
        llm=LLMCall(provider="p", request_model="m", cost_usd=0.5),
    )
    report = ChargebackReport.from_records([rec], dimensions=["team"])
    assert report.rows()[0].dimensions["team"] == "<unattributed>"  # cost never vanishes
    assert report.total_usd() == pytest.approx(0.5)


# --- catalogue ----------------------------------------------------------------
def test_catalogue_declared_active_silent_undeclared() -> None:
    reg = Registry.from_entries(
        [
            {
                "name": "active-agent",
                "version": "1.0",
                "owner": "o",
                "team": "t",
                "lifecycle": "ga",
            },
            {"name": "silent-agent", "version": "1.0", "owner": "o2", "lifecycle": "deprecated"},
        ]
    )
    now = 1_000 * 86_400 * 1_000_000_000  # day 1000 in ns
    records = [
        _agent("t", "prod", "ra"),
        _llm("t", "prod", "ra", 0.4),
    ]
    # relabel the active agent's records to name "active-agent"
    from dataclasses import replace

    records = [replace(r, name="active-agent") if r.kind is Kind.AGENT else r for r in records]
    records.append(_agent("x", "dev", "ru"))
    records[-1] = replace(records[-1], name="rogue-agent")  # undeclared

    # stamp recent end times so the cost falls inside the 30-day window
    catalogue = AgentCatalogue.from_records(
        [replace(r, end_unix_nanos=now) for r in records], registry=reg, now_unix_nanos=now
    )
    by_name = {e.name: e for e in catalogue.entries()}
    assert by_name["active-agent"].active is True
    assert by_name["active-agent"].cost_30d == pytest.approx(0.4)
    assert by_name["silent-agent"].active is False  # declared but no runs
    assert by_name["silent-agent"].lifecycle is Lifecycle.DEPRECATED
    assert by_name["rogue-agent"].declared is False  # active but undeclared
    assert by_name["rogue-agent"].owner is None


def test_catalogue_cost_window_excludes_old() -> None:
    from dataclasses import replace

    reg = Registry.from_entries([{"name": "a", "version": "*", "team": "t"}])
    now = 1_000 * 86_400 * 1_000_000_000
    old = now - 40 * 86_400 * 1_000_000_000  # 40 days ago, outside the 30-day window
    records = [
        replace(_agent("t", "prod", "r1"), name="a", end_unix_nanos=old),
        replace(_llm("t", "prod", "r1", 0.9), end_unix_nanos=old),
    ]
    catalogue = AgentCatalogue.from_records(records, registry=reg, now_unix_nanos=now)
    assert catalogue.entries()[0].cost_30d == 0.0  # old cost excluded from the window


# --- config / install ---------------------------------------------------------
def test_from_config_disabled_stamps_nothing() -> None:
    reg = Registry.from_config({"registry": {"enabled": False, "source": "file", "path": "x"}})
    assert reg.entries == []  # not switched on ⇒ empty


def test_from_config_file(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("agents:\n  - name: a\n    team: t\n")
    reg = Registry.from_config({"registry": {"enabled": True, "source": "file", "path": str(path)}})
    assert reg.resolve("a", "1").team == "t"  # type: ignore[union-attr]


def test_from_config_file_requires_path() -> None:
    with pytest.raises(ValueError, match="requires path"):
        Registry.from_config({"registry": {"enabled": True, "source": "file"}})


def test_from_config_unknown_source() -> None:
    with pytest.raises(ValueError, match="unknown registry source"):
        Registry.from_config({"registry": {"enabled": True, "source": "carrier-pigeon"}})


def test_install_stashes_registry(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("agents:\n  - name: a\n    team: t\n")
    install({"registry": {"enabled": True, "source": "file", "path": str(path)}})
    assert installed_registry() is not None
    assert installed_registry().resolve("a", "1").team == "t"  # type: ignore[union-attr]


def test_agent_entry_lifecycle_default() -> None:
    entry = AgentEntry(name="x")
    assert entry.lifecycle is Lifecycle.GA
    assert entry.version == "*"


def test_from_config_http_requires_url() -> None:
    with pytest.raises(ValueError, match="requires url"):
        Registry.from_config({"registry": {"enabled": True, "source": "http"}})


def test_parse_entries_skips_malformed() -> None:
    from forgesight_registry.source import parse_entries

    assert parse_entries({"agents": "not-a-list"}) == []
    assert parse_entries(42) == []  # not a list at all
    reg = Registry.from_entries(["not-a-dict", {"no_name": "x"}, {"name": "ok", "team": "t"}])
    assert len(reg.entries) == 1  # only the well-formed entry survives
    assert reg.entries[0].name == "ok"


def test_chargeback_ignores_orphan_llm_in_catalogue() -> None:
    # an LLM record whose run_id has no agent run is not attributed to any agent
    reg = Registry.from_entries([{"name": "a", "version": "*", "team": "t"}])
    now = 1_000 * 86_400 * 1_000_000_000
    orphan = _llm("t", "prod", "no-agent-run", 0.9)
    catalogue = AgentCatalogue.from_records([orphan], registry=reg, now_unix_nanos=now)
    assert catalogue.entries()[0].cost_30d == 0.0  # declared "a" has no runs; orphan cost ignored
