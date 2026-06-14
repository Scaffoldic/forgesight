"""Unit tests for the domain model: enums, value types, operation models."""

from __future__ import annotations

import dataclasses
import json

import pytest

from forgesight_api import (
    AgentRun,
    Content,
    Kind,
    LLMCall,
    MCPCall,
    RunStatus,
    Step,
    TokenUsage,
    ToolCall,
    WorkflowRun,
)


def test_enums_serialise_to_their_wire_string() -> None:
    assert RunStatus.BUDGET_EXCEEDED == "budget_exceeded"
    assert Kind.LLM == "llm"
    # str-enum members JSON-encode to their value with no custom encoder
    assert json.dumps({"status": RunStatus.OK}) == '{"status": "ok"}'


def test_token_usage_total_sums_all_five_fields() -> None:
    usage = TokenUsage(input=10, output=20, cache_read=3, cache_creation=4, reasoning=5)
    assert usage.total == 42


def test_token_usage_defaults_to_zero() -> None:
    assert TokenUsage().total == 0


def test_token_usage_is_frozen() -> None:
    usage = TokenUsage(input=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        usage.input = 2  # type: ignore[misc]


def test_llm_call_defaults() -> None:
    call = LLMCall(provider="anthropic", request_model="claude-sonnet-4-5")
    assert call.usage.total == 0
    assert call.cost_usd is None
    assert call.finish_reasons == ()
    assert call.params == {}
    assert call.content is None


def test_content_is_mutable_experimental_container() -> None:
    content = Content()
    content.input_messages = [{"role": "user"}]
    assert content.input_messages == [{"role": "user"}]


def test_tool_call_defaults() -> None:
    tool = ToolCall(name="web_search")
    assert tool.tool_type == "function"
    assert tool.status is RunStatus.RUNNING
    assert tool.duration_ms is None


def test_mcp_call_fields() -> None:
    mcp = MCPCall(server="files", method="tools/call", tool="read_file", session_id="s1")
    assert mcp.method == "tools/call"
    assert mcp.tool == "read_file"


@pytest.mark.parametrize("model_cls", [Step, AgentRun, WorkflowRun])
def test_duration_ms_is_none_while_open(model_cls: type) -> None:
    obj = _make_timed(model_cls, start=1_000_000, end=None)
    assert obj.duration_ms is None


@pytest.mark.parametrize("model_cls", [Step, AgentRun, WorkflowRun])
def test_duration_ms_computed_once_ended(model_cls: type) -> None:
    obj = _make_timed(model_cls, start=1_000_000, end=4_000_000)
    assert obj.duration_ms == pytest.approx(3.0)  # 3_000_000 ns = 3 ms


def test_agent_run_carries_correlation_ids_and_metadata() -> None:
    run = AgentRun(
        agent_name="classifier",
        agent_version="1.2.0",
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        context_id=None,
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        parent_run_id=None,
        status=RunStatus.RUNNING,
        start_unix_nanos=1,
        end_unix_nanos=None,
    )
    run.metadata["team"] = "platform"
    assert run.metadata == {"team": "platform"}
    assert run.status is RunStatus.RUNNING


def _make_timed(model_cls: type, *, start: int, end: int | None) -> object:
    if model_cls is Step:
        return Step(name="s", start_unix_nanos=start, end_unix_nanos=end)
    if model_cls is AgentRun:
        return AgentRun(
            agent_name="a",
            agent_version=None,
            run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
            context_id=None,
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            parent_run_id=None,
            status=RunStatus.RUNNING,
            start_unix_nanos=start,
            end_unix_nanos=end,
        )
    return WorkflowRun(
        workflow_name="w",
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        parent_run_id=None,
        status=RunStatus.RUNNING,
        start_unix_nanos=start,
        end_unix_nanos=end,
    )
