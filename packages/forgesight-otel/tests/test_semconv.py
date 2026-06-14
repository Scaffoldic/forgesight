"""Table-driven tests for the Record → GenAI semconv mapping."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from opentelemetry.trace import SpanKind

from forgesight_api import (
    Content,
    Kind,
    LLMCall,
    MCPCall,
    Record,
    RunStatus,
    TokenUsage,
    ToolCall,
)
from forgesight_otel.semconv import (
    ERROR_TYPE,
    FORGESIGHT_COST_USD,
    FORGESIGHT_RUN_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_AGENT_VERSION,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_CACHE_READ,
    GEN_AI_USAGE_INPUT,
    MCP_METHOD_NAME,
    SemConvMapper,
)

MAPPER = SemConvMapper()
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"


def _record(kind: Kind, name: str, **kw: object) -> Record:
    return Record(
        kind=kind,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id=SPAN,
        parent_span_id=kw.pop("parent_span_id", None),  # type: ignore[arg-type]
        name=name,
        status=kw.pop("status", RunStatus.OK),  # type: ignore[arg-type]
        start_unix_nanos=1_000_000,
        end_unix_nanos=3_000_000,
        attributes=MappingProxyType(kw.pop("attributes", {})),  # type: ignore[arg-type]
        llm=kw.pop("llm", None),  # type: ignore[arg-type]
        tool=kw.pop("tool", None),  # type: ignore[arg-type]
        mcp=kw.pop("mcp", None),  # type: ignore[arg-type]
        error=kw.pop("error", None),  # type: ignore[arg-type]
    )


def test_span_names_and_kinds() -> None:
    cases = [
        (_record(Kind.WORKFLOW, "nightly"), "invoke_workflow nightly", SpanKind.INTERNAL),
        (_record(Kind.AGENT, "classifier"), "invoke_agent classifier", SpanKind.INTERNAL),
        (_record(Kind.STEP, "react-1"), "react-1", SpanKind.INTERNAL),
        (
            _record(Kind.LLM, "claude", llm=LLMCall(provider="anthropic", request_model="claude")),
            "chat claude",
            SpanKind.CLIENT,
        ),
        (
            _record(Kind.TOOL, "search", tool=ToolCall(name="search")),
            "execute_tool search",
            SpanKind.INTERNAL,
        ),
        (
            _record(
                Kind.MCP, "tools/call", mcp=MCPCall(server="f", method="tools/call", tool="rd")
            ),
            "tools/call rd",
            SpanKind.CLIENT,
        ),
        (
            _record(Kind.MCP, "tools/list", mcp=MCPCall(server="f", method="tools/list")),
            "tools/list",
            SpanKind.CLIENT,
        ),
    ]
    for record, name, kind in cases:
        assert MAPPER.span_name(record) == name
        assert MAPPER.span_kind(record) == kind


def test_agent_attributes_and_structured_metadata() -> None:
    rec = _record(
        Kind.AGENT,
        "classifier",
        attributes={"agent.version": "1.2.0", "context.id": "sess-9", "team": "platform"},
    )
    attrs = MAPPER.attributes(rec)
    assert attrs[GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert attrs[GEN_AI_AGENT_NAME] == "classifier"
    assert attrs[GEN_AI_AGENT_VERSION] == "1.2.0"
    assert attrs[GEN_AI_CONVERSATION_ID] == "sess-9"
    assert attrs["team"] == "platform"  # business metadata passes through
    assert attrs[FORGESIGHT_RUN_ID] == rec.run_id


def test_llm_attributes_cost_is_extension_not_gen_ai() -> None:
    llm = LLMCall(
        provider="anthropic",
        request_model="claude-sonnet-4-5",
        usage=TokenUsage(input=100, output=50, cache_read=10),
        cost_usd=0.0123,
        finish_reasons=("stop",),
    )
    attrs = MAPPER.attributes(_record(Kind.LLM, "claude-sonnet-4-5", llm=llm))
    assert attrs[GEN_AI_PROVIDER_NAME] == "anthropic"
    assert attrs[GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-5"
    assert attrs[GEN_AI_USAGE_INPUT] == 100
    assert attrs[GEN_AI_USAGE_CACHE_READ] == 10
    assert attrs[GEN_AI_RESPONSE_FINISH_REASONS] == ["stop"]
    assert attrs[FORGESIGHT_COST_USD] == 0.0123
    assert "gen_ai.usage.cost" not in attrs
    assert "gen_ai.usage.cost_usd" not in attrs
    assert GEN_AI_SYSTEM not in attrs  # legacy off by default


def test_legacy_system_opt_in() -> None:
    llm = LLMCall(provider="openai", request_model="gpt")
    attrs = MAPPER.attributes(_record(Kind.LLM, "gpt", llm=llm), emit_legacy_system=True)
    assert attrs[GEN_AI_SYSTEM] == "openai"


def test_content_gating() -> None:
    llm = LLMCall(
        provider="anthropic",
        request_model="m",
        content=Content(input_messages=[{"role": "user", "text": "hi"}]),
    )
    off = MAPPER.attributes(_record(Kind.LLM, "m", llm=llm), capture_content=False)
    assert GEN_AI_INPUT_MESSAGES not in off
    on = MAPPER.attributes(_record(Kind.LLM, "m", llm=llm), capture_content=True)
    assert "user" in str(on[GEN_AI_INPUT_MESSAGES])


def test_mcp_tools_call_maps_to_execute_tool() -> None:
    mcp = MCPCall(server="files", method="tools/call", tool="read_file", session_id="s1")
    attrs = MAPPER.attributes(_record(Kind.MCP, "tools/call", mcp=mcp))
    assert attrs[MCP_METHOD_NAME] == "tools/call"
    assert attrs[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert attrs[GEN_AI_TOOL_NAME] == "read_file"


def test_tool_attributes() -> None:
    tool = ToolCall(name="search", tool_type="function", call_id="c1", description="web search")
    attrs = MAPPER.attributes(_record(Kind.TOOL, "search", tool=tool))
    assert attrs[GEN_AI_TOOL_NAME] == "search"
    assert attrs[GEN_AI_OPERATION_NAME] == "execute_tool"


def test_error_status_sets_error_type() -> None:
    attrs = MAPPER.attributes(_record(Kind.AGENT, "a", status=RunStatus.ERROR))
    assert attrs[ERROR_TYPE] == "error"
    budget = MAPPER.attributes(_record(Kind.AGENT, "a", status=RunStatus.BUDGET_EXCEEDED))
    assert budget[ERROR_TYPE] == "budget_exceeded"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (1.5, 1.5),
        (True, True),
        ("s", "s"),
        (("a", "b"), ["a", "b"]),
        ({"x": 1}, "{'x': 1}"),
    ],
)
def test_coerce_via_metadata(value: object, expected: object) -> None:
    attrs = MAPPER.attributes(_record(Kind.STEP, "s", attributes={"k": value}))
    assert attrs["k"] == expected


def test_llm_full_optional_fields() -> None:
    llm = LLMCall(
        provider="anthropic",
        request_model="m",
        response_model="m-2",
        response_id="r1",
        usage=TokenUsage(input=1, output=2, cache_creation=3, reasoning=4),
        time_to_first_chunk_ms=120.0,
        params={"temperature": 0.2},
    )
    attrs = MAPPER.attributes(_record(Kind.LLM, "m", llm=llm))
    assert attrs["gen_ai.response.model"] == "m-2"
    assert attrs["gen_ai.response.id"] == "r1"
    assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 3
    assert attrs["gen_ai.usage.reasoning.output_tokens"] == 4
    assert attrs["gen_ai.response.time_to_first_chunk"] == 0.12
    assert attrs["gen_ai.request.temperature"] == 0.2


def test_mcp_session_and_protocol_attributes() -> None:
    mcp = MCPCall(server="f", method="tools/list", session_id="s1", protocol_version="2025-06-18")
    attrs = MAPPER.attributes(_record(Kind.MCP, "tools/list", mcp=mcp))
    assert attrs["mcp.session.id"] == "s1"
    assert attrs["mcp.protocol.version"] == "2025-06-18"


def test_error_info_maps_to_error_type_and_code() -> None:
    from forgesight_api import ErrorInfo

    rec = _record(
        Kind.LLM,
        "m",
        status=RunStatus.ERROR,
        llm=LLMCall(provider="anthropic", request_model="m"),
        error=ErrorInfo(error_type="RateLimitError", message="429", code="rate_limited"),
    )
    attrs = MAPPER.attributes(rec)
    assert attrs[ERROR_TYPE] == "RateLimitError"  # exception class, not the status value
    assert attrs["error.code"] == "rate_limited"
