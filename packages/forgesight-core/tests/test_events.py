"""Tests for lifecycle event emission, ordering, enrichment, and isolation."""

from __future__ import annotations

import pytest

from forgesight_api import EventType, LifecycleEvent
from forgesight_core import configure, get_runtime, reset_runtime, telemetry


class _Recorder:
    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []

    def on_event(self, event: LifecycleEvent) -> None:
        self.events.append(event)


def test_full_lifecycle_event_sequence_and_ordering() -> None:
    rec = _Recorder()
    configure(sync_export=True, listeners=[rec])
    try:
        with telemetry.agent_run("c") as run, run.step("s"):
            with run.llm_call("anthropic", "m"):
                pass
            with run.tool_call("search"):
                pass
    finally:
        reset_runtime()
    kinds = [e.type for e in rec.events]
    assert kinds[0] is EventType.RUN_STARTED
    assert kinds[-1] is EventType.RUN_COMPLETED
    assert EventType.STEP_STARTED in kinds
    assert EventType.LLM_EXECUTED in kinds
    assert EventType.TOOL_EXECUTED in kinds
    assert EventType.STEP_COMPLETED in kinds
    # STEP_STARTED precedes the child LLM/tool events; STEP_COMPLETED follows them
    assert kinds.index(EventType.STEP_STARTED) < kinds.index(EventType.LLM_EXECUTED)
    assert kinds.index(EventType.STEP_COMPLETED) > kinds.index(EventType.TOOL_EXECUTED)


def test_events_carry_trace_and_span_ids() -> None:
    rec = _Recorder()
    configure(sync_export=True, listeners=[rec])
    try:
        with telemetry.agent_run("c") as run:
            assert run.trace_id
    finally:
        reset_runtime()
    started = next(e for e in rec.events if e.type is EventType.RUN_STARTED)
    completed = next(e for e in rec.events if e.type is EventType.RUN_COMPLETED)
    assert started.trace_id == completed.trace_id
    assert started.span_id == completed.span_id
    assert completed.record is not None  # finish events carry the record


def test_run_failed_event_on_exception() -> None:
    rec = _Recorder()
    configure(sync_export=True, listeners=[rec])
    try:
        with pytest.raises(ValueError, match="boom"), telemetry.agent_run("c"):
            raise ValueError("boom")
    finally:
        reset_runtime()
    kinds = [e.type for e in rec.events]
    assert EventType.RUN_FAILED in kinds
    assert EventType.RUN_COMPLETED not in kinds


def test_listeners_fire_in_registration_order() -> None:
    order: list[str] = []

    class _Named:
        def __init__(self, name: str) -> None:
            self.name = name

        def on_event(self, event: LifecycleEvent) -> None:
            if event.type is EventType.RUN_STARTED:
                order.append(self.name)

    configure(sync_export=True, listeners=[_Named("a"), _Named("b"), _Named("c")])
    try:
        with telemetry.agent_run("c"):
            pass
    finally:
        reset_runtime()
    assert order == ["a", "b", "c"]


def test_raising_listener_is_isolated_and_counted() -> None:
    good = _Recorder()

    class _Boom:
        def on_event(self, event: LifecycleEvent) -> None:
            raise RuntimeError("listener down")

    configure(sync_export=True, listeners=[_Boom(), good])
    try:
        with telemetry.agent_run("c"):  # must not raise despite the bad listener
            pass
        assert get_runtime().listener_errors > 0
        assert any(e.type is EventType.RUN_STARTED for e in good.events)  # sibling unaffected
    finally:
        reset_runtime()


def test_deliver_step_events_false_suppresses_step_events() -> None:
    rec = _Recorder()
    configure(sync_export=True, listeners=[rec], deliver_step_events=False)
    try:
        with telemetry.agent_run("c") as run, run.step("s"):
            pass
    finally:
        reset_runtime()
    kinds = [e.type for e in rec.events]
    assert EventType.STEP_STARTED not in kinds
    assert EventType.STEP_COMPLETED not in kinds
    assert EventType.RUN_STARTED in kinds  # non-step events still delivered
