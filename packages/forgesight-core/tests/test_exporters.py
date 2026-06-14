"""Tests for the shipped InMemoryExporter and ConsoleExporter."""

from __future__ import annotations

import io

from forgesight_api import ExportResult, Kind, LLMCall, Record, RunStatus, TokenUsage
from forgesight_core import ConsoleExporter, InMemoryExporter


def _rec(kind: Kind = Kind.STEP, *, llm: LLMCall | None = None) -> Record:
    return Record(
        kind=kind,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="op",
        status=RunStatus.OK,
        start_unix_nanos=1_000_000,
        end_unix_nanos=3_000_000,
        llm=llm,
    )


def test_in_memory_collects_and_clears() -> None:
    exp = InMemoryExporter()
    assert exp.export([_rec(), _rec()]) is ExportResult.SUCCESS
    assert len(exp.records) == 2
    exp.clear()
    assert exp.records == []
    assert exp.force_flush() is True
    exp.shutdown()
    assert exp.records == []


def test_console_writes_to_stream_with_cost() -> None:
    buf = io.StringIO()
    exp = ConsoleExporter(stream=buf)
    llm = LLMCall(provider="anthropic", request_model="m", usage=TokenUsage(input=1), cost_usd=0.5)
    assert exp.export([_rec(Kind.LLM, llm=llm)]) is ExportResult.SUCCESS
    out = buf.getvalue()
    assert "forgesight" in out
    assert "$0.5" in out
    assert exp.force_flush() is True
    exp.shutdown()


def test_console_default_print_path(capsys) -> None:  # type: ignore[no-untyped-def]
    exp = ConsoleExporter()
    exp.export([_rec()])
    assert "forgesight" in capsys.readouterr().out
