"""Tests for the instrumentation scopes — the span tree, status, metadata, pricing."""

from __future__ import annotations

import asyncio

import pytest

from forgesight_api import (
    EventType,
    Kind,
    LifecycleEvent,
    RunStatus,
    TokenUsage,
    is_valid_trace_id,
    is_valid_ulid,
)
from forgesight_core import InMemoryExporter, get_runtime, reset_runtime, telemetry


def _by_kind(mem: InMemoryExporter, kind: Kind) -> list:
    return [r for r in mem.records if r.kind == kind]


def test_agent_run_emits_one_agent_record_with_ok_status(mem: InMemoryExporter) -> None:
    with telemetry.agent_run("classifier", version="1.2.0") as run:
        assert is_valid_ulid(run.run_id)
        assert is_valid_trace_id(run.trace_id)
    records = _by_kind(mem, Kind.AGENT)
    assert len(records) == 1
    rec = records[0]
    assert rec.status is RunStatus.OK
    assert rec.duration_ms is not None
    assert rec.attributes["agent.version"] == "1.2.0"


def test_exception_sets_error_status_and_reraises(mem: InMemoryExporter) -> None:
    with pytest.raises(ValueError, match="bad"), telemetry.agent_run("classifier"):
        raise ValueError("bad")
    rec = _by_kind(mem, Kind.AGENT)[0]
    assert rec.status is RunStatus.ERROR


def test_span_tree_parenting(mem: InMemoryExporter) -> None:
    with telemetry.agent_run("classifier") as run, run.step("iter-1") as step:
        with run.llm_call("anthropic", "claude-sonnet-4-5"):
            pass
        with run.tool_call("web_search"):
            pass
    agent = _by_kind(mem, Kind.AGENT)[0]
    step_rec = _by_kind(mem, Kind.STEP)[0]
    llm = _by_kind(mem, Kind.LLM)[0]
    tool = _by_kind(mem, Kind.TOOL)[0]
    # step's parent is the run; llm and tool parent to the step
    assert step_rec.parent_span_id == agent.span_id
    assert llm.parent_span_id == step.span_id
    assert tool.parent_span_id == step.span_id
    # everything shares the run's trace + run id
    assert {r.trace_id for r in mem.records} == {agent.trace_id}
    assert {r.run_id for r in mem.records} == {agent.run_id}


def test_metadata_scoping_run_vs_call(mem: InMemoryExporter) -> None:
    with telemetry.agent_run("classifier") as run:
        run.set_metadata(team="platform", repo="agentforge")
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            call.set_metadata(prompt_variant="B")
    agent = _by_kind(mem, Kind.AGENT)[0]
    llm = _by_kind(mem, Kind.LLM)[0]
    # run-scope metadata is on the run AND inherited by the llm call
    assert agent.attributes["team"] == "platform"
    assert llm.attributes["team"] == "platform"
    # call-scope metadata is ONLY on that call
    assert llm.attributes["prompt_variant"] == "B"
    assert "prompt_variant" not in agent.attributes


def test_llm_records_usage_and_is_priced(mem: InMemoryExporter) -> None:
    class FixedPricer:
        def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
            return usage.total * 2e-6

    get_runtime().set_pricing(FixedPricer())
    with telemetry.agent_run("c") as run:  # noqa: SIM117
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            call.record_usage(input=100, output=50, cache_read=10)
            call.record_response(finish_reasons=("stop",), response_id="resp-1")
            call.record_params(temperature=0.2)
    llm = _by_kind(mem, Kind.LLM)[0].llm
    assert llm is not None
    assert llm.usage.total == 160
    assert llm.cost_usd == pytest.approx(160 * 2e-6)
    assert llm.finish_reasons == ("stop",)
    assert llm.params == {"temperature": 0.2}
    assert llm.latency_ms is not None


def test_set_cost_takes_precedence_over_pricer(mem: InMemoryExporter) -> None:
    class FixedPricer:
        def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
            return 999.0

    get_runtime().set_pricing(FixedPricer())
    with telemetry.agent_run("c") as run:  # noqa: SIM117
        with run.llm_call("anthropic", "m") as call:
            call.set_cost(0.5)
    assert _by_kind(mem, Kind.LLM)[0].llm.cost_usd == 0.5  # type: ignore[union-attr]


def test_tool_and_mcp_records(mem: InMemoryExporter) -> None:
    with telemetry.agent_run("c") as run:
        with run.tool_call("web_search", tool_type="function"):
            pass
        with run.mcp_call("files", "tools/call", tool="read_file", session_id="s1"):
            pass
    tool = _by_kind(mem, Kind.TOOL)[0].tool
    mcp = _by_kind(mem, Kind.MCP)[0].mcp
    assert tool is not None
    assert tool.name == "web_search"
    assert tool.status is RunStatus.OK
    assert mcp is not None
    assert mcp.method == "tools/call"
    assert mcp.tool == "read_file"


def test_lifecycle_events_emitted(mem: InMemoryExporter) -> None:
    seen: list[str] = []

    class Listener:
        def on_event(self, event: LifecycleEvent) -> None:
            seen.append(event.type)

    get_runtime().add_listener(Listener())
    with telemetry.agent_run("c") as run:  # noqa: SIM117
        with run.llm_call("anthropic", "m"):
            pass
    assert EventType.RUN_STARTED in seen
    assert EventType.LLM_EXECUTED in seen
    assert EventType.RUN_COMPLETED in seen


def test_run_failed_event_on_exception(mem: InMemoryExporter) -> None:
    seen: list[str] = []

    class Listener:
        def on_event(self, event: LifecycleEvent) -> None:
            seen.append(event.type)

    get_runtime().add_listener(Listener())
    with pytest.raises(ValueError, match="x"), telemetry.agent_run("c"):
        raise ValueError("x")
    assert EventType.RUN_FAILED in seen
    assert EventType.RUN_COMPLETED not in seen


def test_current_run_inside_and_outside(mem: InMemoryExporter) -> None:
    assert telemetry.current_run() is None
    with telemetry.agent_run("c") as run:
        assert telemetry.current_run() is run
    assert telemetry.current_run() is None


async def test_async_scope_and_concurrent_parenting(mem: InMemoryExporter) -> None:
    async def tool(run, name: str) -> None:
        async with run.tool_call(name):
            await asyncio.sleep(0)

    async with telemetry.agent_run("fan-out") as run, run.step("parallel"):
        await asyncio.gather(tool(run, "a"), tool(run, "b"), tool(run, "c"))
    step_rec = _by_kind(mem, Kind.STEP)[0]
    tools = _by_kind(mem, Kind.TOOL)
    assert len(tools) == 3
    # every concurrently-opened tool parents to the same step (contextvars copy, P9)
    assert {t.parent_span_id for t in tools} == {step_rec.span_id}


def test_workflow_parents_nested_agent_run(mem: InMemoryExporter) -> None:
    with telemetry.workflow_run("nightly") as wf:  # noqa: SIM117
        with telemetry.agent_run("child") as run:
            assert run.parent_run_id == wf.run_id
            assert run.trace_id == wf.trace_id
    assert len(_by_kind(mem, Kind.WORKFLOW)) == 1
    assert len(_by_kind(mem, Kind.AGENT)) == 1


def test_reset_runtime_clears_state() -> None:
    rt = reset_runtime()
    assert rt.exporters == []
    assert rt.dropped == 0
