"""Tests for the GitHub Actions integration: metadata, bootstrap, summary, OIDC, fallback."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from forgesight_api import (
    EventType,
    Kind,
    LifecycleEvent,
    LLMCall,
    Record,
    RunStatus,
)
from forgesight_core import InMemoryExporter, get_runtime, reset_runtime, telemetry
from forgesight_github import (
    GitHubMetadataInterceptor,
    SummaryCollector,
    bootstrap,
    fetch_oidc_token,
    format_summary,
    github_metadata,
    in_github_actions,
    install,
    pr_number,
    run_exit_hook,
    write_summary,
)
from forgesight_github.bootstrap import _reset_for_tests

CI_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "acme/agents",
    "GITHUB_SHA": "abc123",
    "GITHUB_REF": "refs/pull/42/merge",
    "GITHUB_RUN_ID": "9999",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_WORKFLOW": "review",
    "GITHUB_JOB": "pr-review",
    "GITHUB_ACTOR": "octocat",
    "GITHUB_EVENT_NAME": "pull_request",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # CI itself sets GITHUB_* / GITHUB_STEP_SUMMARY — scrub the ambient env so tests are
    # deterministic whether or not they run inside GitHub Actions.
    for key in (
        *CI_ENV,
        "GITHUB_EVENT_PATH",
        "GITHUB_STEP_SUMMARY",
        "FORGESIGHT_GITHUB_SUMMARY",
        "FORGESIGHT_OTLP_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    _reset_for_tests()
    yield
    reset_runtime()
    _reset_for_tests()


def _apply_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key in (*CI_ENV, "GITHUB_EVENT_PATH", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


# --- github_metadata ----------------------------------------------------------
def test_metadata_maps_all_github_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, CI_ENV)
    meta = github_metadata()
    assert meta["vcs.repository.name"] == "acme/agents"
    assert meta["vcs.ref.head.revision"] == "abc123"
    assert meta["vcs.ref.head.name"] == "refs/pull/42/merge"
    assert meta["cicd.pipeline.run.id"] == "9999"
    assert meta["cicd.pipeline.run.attempt"] == "2"
    assert meta["cicd.pipeline.name"] == "review"
    assert meta["cicd.pipeline.task.name"] == "pr-review"
    assert meta["vcs.change.author"] == "octocat"
    assert meta["cicd.pipeline.run.trigger"] == "pull_request"


def test_pr_number_from_event_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42}}))
    _apply_env(monkeypatch, {**CI_ENV, "GITHUB_EVENT_PATH": str(event)})
    assert github_metadata()["vcs.change.id"] == "42"
    assert pr_number(dict(__import__("os").environ)) == "42"


def test_pr_number_top_level_number(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"number": 7}))
    _apply_env(monkeypatch, {**CI_ENV, "GITHUB_EVENT_PATH": str(event)})
    assert github_metadata()["vcs.change.id"] == "7"


def test_pr_number_absent_for_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42}}))
    _apply_env(
        monkeypatch,
        {**CI_ENV, "GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event)},
    )
    assert "vcs.change.id" not in github_metadata()  # not a PR event ⇒ not fabricated


def test_pr_number_missing_payload_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, {**CI_ENV, "GITHUB_EVENT_PATH": "/nonexistent/event.json"})
    assert "vcs.change.id" not in github_metadata()


def test_pr_number_malformed_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{not json")
    _apply_env(monkeypatch, {**CI_ENV, "GITHUB_EVENT_PATH": str(event)})
    assert "vcs.change.id" not in github_metadata()


def test_capture_env_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, CI_ENV)
    meta = github_metadata(capture_env=["GITHUB_REPOSITORY", "GITHUB_SHA"])
    assert set(meta) == {"vcs.repository.name", "vcs.ref.head.revision"}  # actor dropped


def test_metadata_via_explicit_env() -> None:
    meta = github_metadata(env={"GITHUB_REPOSITORY": "x/y"})
    assert meta == {"vcs.repository.name": "x/y"}


# --- interceptor --------------------------------------------------------------
def _record(**attrs: object) -> Record:
    from types import MappingProxyType

    return Record(
        kind=Kind.AGENT,
        run_id="r",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="c",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType(dict(attrs)),
    )


def test_interceptor_merges_without_clobbering() -> None:
    interceptor = GitHubMetadataInterceptor({"vcs.repository.name": "acme/agents", "team": "ci"})
    out = interceptor.intercept(_record(team="explicit"))
    assert out is not None
    assert out.attributes["vcs.repository.name"] == "acme/agents"  # added
    assert out.attributes["team"] == "explicit"  # per-call metadata wins


def test_interceptor_empty_is_passthrough() -> None:
    record = _record()
    assert GitHubMetadataInterceptor({}).intercept(record) is record


# --- bootstrap ----------------------------------------------------------------
def test_bootstrap_attaches_metadata_to_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, CI_ENV)
    bootstrap(write_summary=False, _register_exit=False)
    exporter = InMemoryExporter()
    runtime = get_runtime()
    runtime.add_exporter(exporter)
    with telemetry.agent_run("classifier"):
        pass
    runtime.force_flush()
    run = next(r for r in exporter.records if r.kind is Kind.AGENT)
    assert run.attributes["vcs.repository.name"] == "acme/agents"
    assert run.attributes["cicd.pipeline.name"] == "review"


def test_bootstrap_extra_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, CI_ENV)
    bootstrap(write_summary=False, extra_metadata={"team": "payments"}, _register_exit=False)
    exporter = InMemoryExporter()
    get_runtime().add_exporter(exporter)
    with telemetry.agent_run("c"):
        pass
    get_runtime().force_flush()
    run = next(r for r in exporter.records if r.kind is Kind.AGENT)
    assert run.attributes["team"] == "payments"


def test_bootstrap_not_in_ci_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _apply_env(monkeypatch, {})  # GITHUB_ACTIONS unset
    assert in_github_actions() is False
    with caplog.at_level("WARNING"):
        bootstrap(write_summary=False, _register_exit=False)
        bootstrap(write_summary=False, _register_exit=False)
    warnings = [r for r in caplog.records if "falls back to plain configure" in r.message]
    assert len(warnings) == 1  # warned once, not twice


def test_bootstrap_not_in_ci_no_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, {"GITHUB_REPOSITORY": "x/y"})  # set but GITHUB_ACTIONS unset
    bootstrap(write_summary=False, _register_exit=False)
    exporter = InMemoryExporter()
    get_runtime().add_exporter(exporter)
    with telemetry.agent_run("c"):
        pass
    get_runtime().force_flush()
    run = next(r for r in exporter.records if r.kind is Kind.AGENT)
    assert "vcs.repository.name" not in run.attributes  # no CI metadata outside CI


# --- summary collector --------------------------------------------------------
def _event(event_type: EventType, record: Record | None = None) -> LifecycleEvent:
    return LifecycleEvent(type=event_type, run_id="r", unix_nanos=1, record=record)


def test_summary_collector_tallies() -> None:
    collector = SummaryCollector()
    llm = Record(
        kind=Kind.LLM,
        run_id="r",
        trace_id="t",
        span_id="s",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=0,
        end_unix_nanos=1_000_000,
        llm=LLMCall(provider="anthropic", request_model="m", cost_usd=0.05),
    )
    run = _record()
    collector.on_event(_event(EventType.LLM_EXECUTED, llm))
    collector.on_event(_event(EventType.TOOL_EXECUTED))
    collector.on_event(_event(EventType.MCP_EXECUTED))
    collector.on_event(_event(EventType.RUN_COMPLETED, run))
    assert collector.total_cost_usd == 0.05
    assert collector.n_tool_calls == 2
    assert collector.status() == "ok"


def test_summary_status_error_on_failed_run() -> None:
    collector = SummaryCollector()
    failed = Record(
        kind=Kind.AGENT,
        run_id="r",
        trace_id="t",
        span_id="s",
        parent_span_id=None,
        name="c",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    collector.on_event(_event(EventType.RUN_FAILED, failed))
    assert collector.status() == "error"


def test_summary_status_no_runs() -> None:
    assert SummaryCollector().status() == "no runs"


def test_format_summary_single_and_multi() -> None:
    collector = SummaryCollector()
    collector.on_event(_event(EventType.RUN_COMPLETED, _record()))
    single = format_summary(collector, DEFAULT := ("status", "cost_usd", "n_tool_calls"))
    assert "### 🤖 ForgeSight agent run" in single
    assert "- **status**: ok" in single
    collector.on_event(_event(EventType.RUN_COMPLETED, _record()))
    multi = format_summary(collector, DEFAULT)
    assert "runs" in multi  # rollup shows run count for multi-run jobs


def test_write_summary_to_file(tmp_path: Path) -> None:
    collector = SummaryCollector()
    collector.on_event(_event(EventType.RUN_COMPLETED, _record()))
    summary = tmp_path / "summary.md"
    assert write_summary(collector, ("status", "cost_usd"), path=str(summary)) is True
    assert "status" in summary.read_text()


def test_write_summary_no_target_is_false() -> None:
    assert write_summary(SummaryCollector(), ("status",), path=None) is False


def test_write_summary_failure_is_isolated(tmp_path: Path) -> None:
    # a directory path can't be opened for append ⇒ returns False, never raises (P6)
    assert write_summary(SummaryCollector(), ("status",), path=str(tmp_path)) is False


# --- exit hook ----------------------------------------------------------------
def test_run_exit_hook_flushes_and_writes_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "step_summary.md"
    _apply_env(monkeypatch, {**CI_ENV, "GITHUB_STEP_SUMMARY": str(summary)})
    bootstrap(_register_exit=False)
    exporter = InMemoryExporter()
    runtime = get_runtime()
    runtime.add_exporter(exporter)
    collector = next(c for c in runtime.listeners if isinstance(c, SummaryCollector))
    with (
        telemetry.agent_run("classifier"),
        telemetry.current_run().llm_call("anthropic", "m") as call,
    ):  # type: ignore[union-attr]
        call.record_usage(input=10, output=5)
    run_exit_hook(runtime, collector, ("status", "cost_usd", "n_tool_calls"))
    assert summary.exists()
    assert "ForgeSight agent run" in summary.read_text()


def test_bootstrap_summary_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, {**CI_ENV, "FORGESIGHT_GITHUB_SUMMARY": "false"})
    bootstrap(_register_exit=False)
    assert not any(isinstance(c, SummaryCollector) for c in get_runtime().listeners)


# --- OIDC ---------------------------------------------------------------------
def test_oidc_absent_endpoint_returns_none() -> None:
    assert fetch_oidc_token(env={}) is None


def test_oidc_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"value": "tok-123"}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    token = fetch_oidc_token(
        audience="collector",
        env={
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://runner/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "bearer",
        },
    )
    assert token == "tok-123"


def test_oidc_fetch_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def _boom(*a: object, **k: object) -> object:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    token = fetch_oidc_token(
        env={
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://runner/token",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "bearer",
        }
    )
    assert token is None


def test_bootstrap_oidc_fallback_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _apply_env(monkeypatch, CI_ENV)  # no id-token endpoint
    with caplog.at_level("WARNING"):
        bootstrap(write_summary=False, oidc=True, _register_exit=False)
    assert any("OIDC requested but no runner id-token" in r.message for r in caplog.records)


# --- install ------------------------------------------------------------------
def test_install_stashes_config_and_summary_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_env(monkeypatch, CI_ENV)
    assert install({"enabled": True, "write_summary": False}) is True
    bootstrap(_register_exit=False)  # write_summary default reads installed config ⇒ off
    assert not any(isinstance(c, SummaryCollector) for c in get_runtime().listeners)


def test_install_disabled_returns_false() -> None:
    assert install({"enabled": False}) is False


def test_bootstrap_registers_exit_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    _apply_env(monkeypatch, CI_ENV)
    registered: list[object] = []
    bs = sys.modules["forgesight_github.bootstrap"]  # function shadows submodule on the package

    monkeypatch.setattr(bs.atexit, "register", lambda fn, *a: registered.append((fn, a)))
    bootstrap(write_summary=False)  # _register_exit defaults True
    assert registered
    assert registered[0][0] is run_exit_hook


def test_bootstrap_oidc_success_sets_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys

    _apply_env(monkeypatch, CI_ENV)
    monkeypatch.delenv("FORGESIGHT_OTLP_TOKEN", raising=False)
    bs = sys.modules["forgesight_github.bootstrap"]

    monkeypatch.setattr(bs, "fetch_oidc_token", lambda: "tok-xyz")
    bootstrap(write_summary=False, oidc=True, _register_exit=False)
    assert os.environ["FORGESIGHT_OTLP_TOKEN"] == "tok-xyz"
    monkeypatch.delenv("FORGESIGHT_OTLP_TOKEN", raising=False)
