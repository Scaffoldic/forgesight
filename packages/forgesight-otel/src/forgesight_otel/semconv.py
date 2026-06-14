"""The single source of truth for ForgeSight's OTLP wire format.

Maps a :class:`~forgesight_api.Record` onto a span name, an OTel ``SpanKind``, and the
GenAI semantic-convention attribute set, per ``docs/design/otel-semantic-conventions.md``.
Re-pinning the spec changes only this module (P5, ADR-0004).

The conventions live in ``open-telemetry/semantic-conventions-genai`` and are all at
``Development`` stability with no tagged release, so we pin to a commit and stamp the
version on every span's Resource.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from opentelemetry.trace import SpanKind
from opentelemetry.util.types import AttributeValue

from forgesight_api import Kind, Record, RunStatus

# --- pinning ---------------------------------------------------------------
SEMCONV_COMMIT = "open-telemetry/semantic-conventions-genai@main"
SEMCONV_VERSION = "genai-dev-2026-06"

# --- attribute keys (locked to the design doc) -----------------------------
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_SYSTEM = "gen_ai.system"  # legacy; opt-in only
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_VERSION = "gen_ai.agent.version"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_PREFIX = "gen_ai.request."
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_RESPONSE_TTFC = "gen_ai.response.time_to_first_chunk"
GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_CACHE_CREATION = "gen_ai.usage.cache_creation.input_tokens"
GEN_AI_USAGE_REASONING = "gen_ai.usage.reasoning.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
MCP_METHOD_NAME = "mcp.method.name"
MCP_SESSION_ID = "mcp.session.id"
MCP_PROTOCOL_VERSION = "mcp.protocol.version"
ERROR_TYPE = "error.type"

# extensions (namespaced — OTel defines none of these)
FORGESIGHT_RUN_ID = "forgesight.run.id"
FORGESIGHT_PARENT_RUN_ID = "forgesight.parent.run_id"
FORGESIGHT_COST_USD = "forgesight.usage.cost_usd"
FORGESIGHT_SEMCONV_VERSION = "forgesight.semconv_version"

# operation.name values
OP_INVOKE_AGENT = "invoke_agent"
OP_INVOKE_WORKFLOW = "invoke_workflow"
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"

_MCP_TOOLS_CALL = "tools/call"
# structured run fields that feat-002 stashes in Record.attributes → mapped to gen_ai.*
_STRUCTURED = {
    "agent.version": GEN_AI_AGENT_VERSION,
    "context.id": GEN_AI_CONVERSATION_ID,
    "parent.run_id": FORGESIGHT_PARENT_RUN_ID,
}
_OK_STATUSES = frozenset({RunStatus.OK, RunStatus.RUNNING})


def _coerce(value: object) -> AttributeValue:
    """Coerce an arbitrary value to a valid OTel attribute value."""
    if isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return str(value)


class SemConvMapper:
    """Pure Record → (span name, kind, attributes) mapping. No I/O, no OTel SDK state."""

    def span_name(self, record: Record) -> str:
        if record.kind is Kind.WORKFLOW:
            return f"{OP_INVOKE_WORKFLOW} {record.name}"
        if record.kind is Kind.AGENT:
            return f"{OP_INVOKE_AGENT} {record.name}"
        if record.kind is Kind.LLM:
            return f"{OP_CHAT} {record.name}"
        if record.kind is Kind.TOOL:
            return f"{OP_EXECUTE_TOOL} {record.name}"
        if record.kind is Kind.MCP and record.mcp is not None:
            if record.mcp.method == _MCP_TOOLS_CALL and record.mcp.tool:
                return f"{_MCP_TOOLS_CALL} {record.mcp.tool}"
            return record.mcp.method
        return record.name  # STEP (custom name)

    def span_kind(self, record: Record) -> SpanKind:
        if record.kind in (Kind.LLM, Kind.MCP):
            return SpanKind.CLIENT
        return SpanKind.INTERNAL

    def attributes(
        self, record: Record, *, capture_content: bool = False, emit_legacy_system: bool = False
    ) -> dict[str, AttributeValue]:
        attrs: dict[str, AttributeValue] = {FORGESIGHT_RUN_ID: record.run_id}
        self._map_metadata(record.attributes, attrs)
        if record.kind is Kind.AGENT:
            attrs[GEN_AI_OPERATION_NAME] = OP_INVOKE_AGENT
            attrs[GEN_AI_AGENT_NAME] = record.name
        elif record.kind is Kind.WORKFLOW:
            attrs[GEN_AI_OPERATION_NAME] = OP_INVOKE_WORKFLOW
        elif record.kind is Kind.LLM and record.llm is not None:
            self._map_llm(record, attrs, capture_content, emit_legacy_system)
        elif record.kind is Kind.TOOL and record.tool is not None:
            attrs[GEN_AI_OPERATION_NAME] = OP_EXECUTE_TOOL
            attrs[GEN_AI_TOOL_NAME] = record.tool.name
            attrs[GEN_AI_TOOL_TYPE] = record.tool.tool_type
            if record.tool.call_id is not None:
                attrs[GEN_AI_TOOL_CALL_ID] = record.tool.call_id
            if record.tool.description is not None:
                attrs[GEN_AI_TOOL_DESCRIPTION] = record.tool.description
        elif record.kind is Kind.MCP and record.mcp is not None:
            self._map_mcp(record, attrs)
        if record.status not in _OK_STATUSES:
            attrs[ERROR_TYPE] = record.status.value
        return attrs

    # --- helpers ----------------------------------------------------------
    def _map_metadata(self, source: Mapping[str, object], attrs: dict[str, AttributeValue]) -> None:
        for key, value in source.items():
            mapped = _STRUCTURED.get(key)
            attrs[mapped if mapped is not None else key] = _coerce(value)

    def _map_llm(
        self,
        record: Record,
        attrs: dict[str, AttributeValue],
        capture_content: bool,
        emit_legacy_system: bool,
    ) -> None:
        llm = record.llm
        assert llm is not None
        attrs[GEN_AI_OPERATION_NAME] = OP_CHAT
        attrs[GEN_AI_PROVIDER_NAME] = llm.provider
        if emit_legacy_system:
            attrs[GEN_AI_SYSTEM] = llm.provider
        attrs[GEN_AI_REQUEST_MODEL] = llm.request_model
        if llm.response_model is not None:
            attrs[GEN_AI_RESPONSE_MODEL] = llm.response_model
        if llm.response_id is not None:
            attrs[GEN_AI_RESPONSE_ID] = llm.response_id
        usage = llm.usage
        attrs[GEN_AI_USAGE_INPUT] = usage.input
        attrs[GEN_AI_USAGE_OUTPUT] = usage.output
        if usage.cache_read:
            attrs[GEN_AI_USAGE_CACHE_READ] = usage.cache_read
        if usage.cache_creation:
            attrs[GEN_AI_USAGE_CACHE_CREATION] = usage.cache_creation
        if usage.reasoning:
            attrs[GEN_AI_USAGE_REASONING] = usage.reasoning
        if llm.finish_reasons:
            attrs[GEN_AI_RESPONSE_FINISH_REASONS] = list(llm.finish_reasons)
        if llm.time_to_first_chunk_ms is not None:
            attrs[GEN_AI_RESPONSE_TTFC] = llm.time_to_first_chunk_ms / 1000.0
        if llm.cost_usd is not None:
            attrs[FORGESIGHT_COST_USD] = llm.cost_usd
        for key, value in llm.params.items():
            attrs[f"{GEN_AI_REQUEST_PREFIX}{key}"] = _coerce(value)
        if capture_content and llm.content is not None:
            self._map_content(llm.content, attrs)

    @staticmethod
    def _map_content(content: object, attrs: dict[str, AttributeValue]) -> None:
        # content is the experimental forgesight_api.Content container (P7-gated).
        for field, key in (
            ("input_messages", GEN_AI_INPUT_MESSAGES),
            ("output_messages", GEN_AI_OUTPUT_MESSAGES),
            ("system_instructions", GEN_AI_SYSTEM_INSTRUCTIONS),
        ):
            value = getattr(content, field, None)
            if value is not None:
                attrs[key] = json.dumps(value, default=str)

    def _map_mcp(self, record: Record, attrs: dict[str, AttributeValue]) -> None:
        mcp = record.mcp
        assert mcp is not None
        attrs[MCP_METHOD_NAME] = mcp.method
        if mcp.session_id is not None:
            attrs[MCP_SESSION_ID] = mcp.session_id
        if mcp.protocol_version is not None:
            attrs[MCP_PROTOCOL_VERSION] = mcp.protocol_version
        if mcp.method == _MCP_TOOLS_CALL:
            attrs[GEN_AI_OPERATION_NAME] = OP_EXECUTE_TOOL
            if mcp.tool is not None:
                attrs[GEN_AI_TOOL_NAME] = mcp.tool
