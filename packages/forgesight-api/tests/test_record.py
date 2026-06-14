"""Unit tests for the exporter-facing value types."""

from __future__ import annotations

import dataclasses

import pytest

from forgesight_api import (
    EventType,
    ExportResult,
    Kind,
    LifecycleEvent,
    LLMCall,
    Record,
    RunStatus,
    TokenUsage,
)


def _record(*, start: int = 1_000_000, end: int | None = 5_000_000) -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="chat claude-sonnet-4-5",
        status=RunStatus.OK,
        start_unix_nanos=start,
        end_unix_nanos=end,
        llm=LLMCall(
            provider="anthropic",
            request_model="claude-sonnet-4-5",
            usage=TokenUsage(input=10, output=5),
        ),
    )


def test_record_duration_ms() -> None:
    assert _record(start=1_000_000, end=5_000_000).duration_ms == pytest.approx(4.0)


def test_record_duration_none_while_open() -> None:
    assert _record(end=None).duration_ms is None


def test_record_is_frozen() -> None:
    rec = _record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.status = RunStatus.ERROR  # type: ignore[misc]


def test_record_attributes_default_is_readonly_mapping() -> None:
    rec = _record()
    assert rec.attributes == {}
    with pytest.raises(TypeError):
        rec.attributes["x"] = 1  # type: ignore[index]


def test_export_result_values() -> None:
    assert ExportResult.SUCCESS.value == 0
    assert ExportResult.FAILURE.value == 1


def test_event_type_is_open_str_enum() -> None:
    assert EventType.RUN_STARTED == "run_started"
    assert EventType.MCP_EXECUTED == "mcp_executed"


def test_lifecycle_event_carries_its_record() -> None:
    rec = _record()
    event = LifecycleEvent(type=EventType.LLM_EXECUTED, run_id=rec.run_id, unix_nanos=5, record=rec)
    assert event.record is rec
    assert event.attributes == {}
