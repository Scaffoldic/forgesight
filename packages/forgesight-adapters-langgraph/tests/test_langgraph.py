"""Tests for the LangGraph adapter: callback→span mapping, nesting, usage, conformance."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from langchain_core.outputs import LLMResult

from forgesight_adapters_langgraph import ForgeSightLangChainHandler, LangGraphAdapter
from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, configure, reset_runtime, tool_call_active
from forgesight_core.testing.conformance import run_adapter_conformance


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        yield exporter
    finally:
        reset_runtime()


def _llm_result(prompt: int = 10, completion: int = 5) -> LLMResult:
    return LLMResult(
        generations=[],
        llm_output={"token_usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
    )


# --- conformance --------------------------------------------------------------
def test_conformance() -> None:
    run_adapter_conformance(LangGraphAdapter)


def test_adapter_name() -> None:
    assert LangGraphAdapter().name == "langgraph"


# --- canonical mapping --------------------------------------------------------
def test_canonical_graph_run_maps_to_span_tree(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    root, node, llm, tool = uuid4(), uuid4(), uuid4(), uuid4()

    h.on_chain_start({"name": "graph"}, {}, run_id=root)  # graph invoke → agent_run
    h.on_chain_start({}, {}, run_id=node, parent_run_id=root, metadata={"langgraph_node": "review"})
    h.on_chat_model_start(
        {},
        [],
        run_id=llm,
        parent_run_id=node,
        metadata={"ls_provider": "anthropic", "ls_model_name": "claude-sonnet-4-5"},
    )
    h.on_llm_end(_llm_result(), run_id=llm)
    h.on_tool_start({"name": "search"}, "q", run_id=tool, parent_run_id=node)
    h.on_tool_end("result", run_id=tool)
    h.on_chain_end({}, run_id=node)
    h.on_chain_end({}, run_id=root)

    by_kind = {r.kind: r for r in sink.records}
    assert set(by_kind) == {Kind.AGENT, Kind.STEP, Kind.LLM, Kind.TOOL}
    run, step = by_kind[Kind.AGENT], by_kind[Kind.STEP]
    llm_rec, tool_rec = by_kind[Kind.LLM], by_kind[Kind.TOOL]
    assert run.name == "graph"
    assert step.name == "review"
    assert step.parent_span_id == run.span_id  # node nests under the graph run
    assert llm_rec.parent_span_id == step.span_id  # llm nests under the node
    assert tool_rec.parent_span_id == step.span_id
    assert llm_rec.llm is not None
    assert llm_rec.llm.provider == "anthropic"
    assert llm_rec.llm.usage.input == 10
    assert llm_rec.llm.usage.output == 5
    assert tool_rec.tool is not None
    assert tool_rec.tool.name == "search"


def test_usage_from_generations_usage_metadata(sink: InMemoryExporter) -> None:
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration

    message = AIMessage(
        content="hi", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=None)
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_llm_start({"kwargs": {"model": "m"}}, ["hi"], run_id=rid)
    h.on_llm_end(result, run_id=rid)
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.llm is not None
    assert llm.llm.usage.input == 7
    assert llm.llm.usage.output == 3


def test_chain_error_marks_run_error(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_chain_start({"name": "g"}, {}, run_id=rid)
    h.on_chain_error(RuntimeError("graph blew up"), run_id=rid)
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.status is RunStatus.ERROR
    assert run.error is not None
    assert run.error.error_type == "RuntimeError"


def test_tool_error_marks_tool_error(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    root, tool = uuid4(), uuid4()
    h.on_chain_start({"name": "g"}, {}, run_id=root)
    h.on_tool_start({"name": "search"}, "q", run_id=tool, parent_run_id=root)
    h.on_tool_error(TimeoutError("slow"), run_id=tool)
    h.on_chain_end({}, run_id=root)
    tool_rec = next(r for r in sink.records if r.kind is Kind.TOOL)
    assert tool_rec.status is RunStatus.ERROR


def test_unknown_provider_and_model_fallback(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_llm_start({}, ["hi"], run_id=rid)  # no metadata, no model in serialized
    h.on_llm_end(_llm_result(0, 0), run_id=rid)
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.llm is not None
    assert llm.llm.provider == "unknown"
    assert llm.llm.request_model == "unknown"


# --- no double-instrument -----------------------------------------------------
def test_defers_to_inner_tool_span(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    root, tool = uuid4(), uuid4()
    h.on_chain_start({"name": "g"}, {}, run_id=root)
    with tool_call_active():  # an MCP tools/call span is already open
        h.on_tool_start({"name": "search"}, "q", run_id=tool, parent_run_id=root)
        h.on_tool_end("result", run_id=tool)
    h.on_chain_end({}, run_id=root)
    assert [r for r in sink.records if r.kind is Kind.TOOL] == []  # no second execute_tool span


# --- adapter subscription (real langchain hook) -------------------------------
def test_instrument_registers_global_handler() -> None:
    adapter = LangGraphAdapter()
    adapter.instrument()
    try:
        assert adapter.is_instrumented() is True
        assert adapter._var.get() is adapter.handler  # handler is the active inheritable callback
    finally:
        adapter.uninstrument()
    assert adapter._var.get() is None  # cleared on uninstrument


def test_llm_error_marks_error(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_llm_start({"kwargs": {"model": "m"}}, ["hi"], run_id=rid)
    h.on_llm_error(RuntimeError("rate limited"), run_id=rid)
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.status is RunStatus.ERROR
    assert llm.error is not None
    assert llm.error.error_type == "RuntimeError"


def test_chain_name_from_serialized_id(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_chain_start({"id": ["langchain", "chains", "MyChain"]}, {}, run_id=rid)
    h.on_chain_end({}, run_id=rid)
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.name == "MyChain"


def test_chain_name_default_when_empty(sink: InMemoryExporter) -> None:
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_chain_start({}, {}, run_id=rid)  # no name, no id, no metadata
    h.on_chain_end({}, run_id=rid)
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.name == "chain"


def test_generations_without_usage(sink: InMemoryExporter) -> None:
    from langchain_core.outputs import Generation

    result = LLMResult(generations=[[Generation(text="hi")]], llm_output=None)
    h = ForgeSightLangChainHandler()
    rid = uuid4()
    h.on_llm_start({"kwargs": {"model": "m"}}, ["hi"], run_id=rid)
    h.on_llm_end(result, run_id=rid)
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.llm is not None
    assert llm.llm.usage.input == 0  # no usage anywhere ⇒ zero (cost stays null)
