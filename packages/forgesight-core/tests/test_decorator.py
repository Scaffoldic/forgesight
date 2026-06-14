"""Tests for the @instrument decorator."""

from __future__ import annotations

import pytest

from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, instrument


def test_instrument_tool_sync(mem: InMemoryExporter) -> None:
    @instrument(kind="tool", name="search")
    def search(q: str) -> str:
        return q.upper()

    assert search("hi") == "HI"
    tools = [r for r in mem.records if r.kind == Kind.TOOL]
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].status is RunStatus.OK


async def test_instrument_agent_async(mem: InMemoryExporter) -> None:
    @instrument(kind=Kind.AGENT, version="2.0.0")
    async def classify(issue: str) -> str:
        return "bug"

    assert await classify("crash") == "bug"
    agents = [r for r in mem.records if r.kind == Kind.AGENT]
    assert len(agents) == 1
    assert agents[0].attributes["agent.version"] == "2.0.0"


def test_instrument_step_default_name(mem: InMemoryExporter) -> None:
    @instrument(kind="step")
    def phase() -> int:
        return 1

    assert phase() == 1
    steps = [r for r in mem.records if r.kind == Kind.STEP]
    assert len(steps) == 1
    assert "phase" in steps[0].name


def test_instrument_records_error_and_reraises(mem: InMemoryExporter) -> None:
    @instrument(kind="tool", name="boom")
    def boom() -> None:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError, match="x"):
        boom()
    tool = next(r for r in mem.records if r.kind == Kind.TOOL)
    assert tool.status is RunStatus.ERROR


def test_instrument_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="agent, step, tool"):
        instrument(kind="llm")
