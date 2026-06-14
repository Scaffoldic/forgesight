"""Tests for the async export pipeline: worker, batching, backpressure, sampling.

Backpressure is tested with the worker disabled (white-box) so the queue can fill
deterministically; the worker + e2e tests exercise the real background thread with a
short schedule delay.
"""

from __future__ import annotations

import time

import pytest

from forgesight_api import Kind, Record, RunStatus
from forgesight_core import (
    InMemoryExporter,
    Runtime,
    configure,
    get_runtime,
    reset_runtime,
    telemetry,
)
from forgesight_core.processor import RuntimeConfig


def _rec(name: str = "r", *, trace_id: str = "4bf92f3577b34da6a3ce929d0e0e4736") -> Record:
    return Record(
        kind=Kind.STEP,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=trace_id,
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name=name,
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="max_export_batch_size"):
        RuntimeConfig(max_queue_size=10, max_export_batch_size=20)
    with pytest.raises(ValueError, match="sample_rate"):
        RuntimeConfig(sample_rate=1.5)


def test_background_worker_drains_without_force_flush() -> None:
    mem = InMemoryExporter()
    configure(exporters=[mem], schedule_delay_millis=10)  # async, fast tick
    try:
        get_runtime().emit_record(_rec("auto"))
        deadline = time.monotonic() + 2.0
        while not mem.records and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [r.name for r in mem.records] == ["auto"]
    finally:
        reset_runtime()


def test_queue_full_drops_newest_and_counts() -> None:
    rt = Runtime(RuntimeConfig(max_queue_size=2, max_export_batch_size=2))
    rt._shutdown = True  # prevent the worker from starting/draining (white-box)
    rt.add_exporter(InMemoryExporter())
    rt.emit_record(_rec("1"))
    rt.emit_record(_rec("2"))
    rt.emit_record(_rec("3"))  # queue full ⇒ dropped
    assert rt.dropped == 1


def test_sampling_drops_whole_traces() -> None:
    mem = InMemoryExporter()
    rt = reset_runtime(RuntimeConfig(sample_rate=0.0, sync_export=True))
    rt.add_exporter(mem)
    rt.emit_record(_rec("x"))
    assert mem.records == []
    assert rt.sampled_out == 1
    reset_runtime()


def test_sampling_keeps_everything_at_full_rate() -> None:
    mem = InMemoryExporter()
    rt = reset_runtime(RuntimeConfig(sample_rate=1.0, sync_export=True))
    rt.add_exporter(mem)
    rt.emit_record(_rec("x"))
    assert [r.name for r in mem.records] == ["x"]
    reset_runtime()


def test_sampling_is_deterministic_per_trace() -> None:
    rt = Runtime(RuntimeConfig(sample_rate=0.5))
    low = "00000000000000000000000000000000"  # bucket 0 ⇒ kept at 0.5
    high = "ffffffffffffffff0000000000000000"  # top bucket ⇒ dropped at 0.5
    assert rt._sampled(low) is True
    assert rt._sampled(high) is False
    assert rt._sampled("not-hex-zzzz") is True  # unparseable ⇒ never silently drop


async def test_async_e2e_pipeline() -> None:
    """End-to-end: an agent run flows through the real async worker to the exporter."""
    mem = InMemoryExporter()
    configure(exporters=[mem], schedule_delay_millis=10)  # async default + fast tick
    try:
        async with telemetry.agent_run("e2e", version="1.0.0") as run, run.step("phase"):
            async with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=10, output=5)
            async with run.tool_call("search"):
                pass
        assert get_runtime().force_flush() is True
        kinds = sorted({r.kind for r in mem.records})
        assert kinds == [Kind.AGENT, Kind.LLM, Kind.STEP, Kind.TOOL]
    finally:
        reset_runtime()


def test_shutdown_is_idempotent() -> None:
    rt = reset_runtime()
    rt.add_exporter(InMemoryExporter())
    rt.emit_record(_rec())
    rt.shutdown()
    rt.shutdown()  # second call is a no-op


def test_configure_applies_all_overrides() -> None:
    class _L:
        def on_event(self, event: object) -> None:
            return None

    class _I:
        def intercept(self, record: Record) -> Record | None:
            return record

    class _P:
        def price(self, provider: str, model: str, usage: object) -> float | None:
            return 1.0

    rt = configure(
        service_name="svc",
        capture_content=True,
        default_tool_type="rest",
        sample_rate=0.5,
        sync_export=True,
        max_queue_size=100,
        max_export_batch_size=50,
        schedule_delay_millis=20,
        exporters=[InMemoryExporter()],
        interceptors=[_I()],
        listeners=[_L()],
        pricing=_P(),
    )
    assert rt.config.service_name == "svc"
    assert rt.config.capture_content is True
    assert rt.config.default_tool_type == "rest"
    assert rt.config.sample_rate == 0.5
    assert rt.config.sync_export is True
    assert rt.config.max_queue_size == 100
    assert rt.config.max_export_batch_size == 50
    assert rt.config.schedule_delay_millis == 20
    assert len(rt.interceptors) == 1
    assert len(rt.listeners) == 1
    assert rt.pricing is not None
    reset_runtime()
