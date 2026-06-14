"""Tests for the dispatch runtime: interceptors, fault isolation, events, flush."""

from __future__ import annotations

from collections.abc import Sequence

from forgesight_api import (
    EventType,
    ExportResult,
    Kind,
    LifecycleEvent,
    Record,
    RunStatus,
)
from forgesight_core import InMemoryExporter, reset_runtime


def _rec(name: str = "r") -> Record:
    return Record(
        kind=Kind.STEP,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name=name,
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )


def test_emit_record_reaches_exporter() -> None:
    rt = reset_runtime()
    mem = InMemoryExporter()
    rt.add_exporter(mem)
    rt.emit_record(_rec("a"))
    assert [r.name for r in mem.records] == ["a"]


def test_interceptor_can_drop_and_is_counted() -> None:
    rt = reset_runtime()
    mem = InMemoryExporter()
    rt.add_exporter(mem)

    class Dropper:
        def intercept(self, record: Record) -> Record | None:
            return None

    rt.add_interceptor(Dropper())
    rt.emit_record(_rec())
    assert mem.records == []
    assert rt.dropped == 1


def test_interceptor_can_replace_record() -> None:
    rt = reset_runtime()
    mem = InMemoryExporter()
    rt.add_exporter(mem)

    class Rename:
        def intercept(self, record: Record) -> Record | None:
            return _rec("renamed")

    rt.add_interceptor(Rename())
    rt.emit_record(_rec("orig"))
    assert [r.name for r in mem.records] == ["renamed"]


def test_raising_interceptor_is_isolated() -> None:
    rt = reset_runtime()
    mem = InMemoryExporter()
    rt.add_exporter(mem)

    class Boom:
        def intercept(self, record: Record) -> Record | None:
            raise RuntimeError("boom")

    rt.add_interceptor(Boom())
    rt.emit_record(_rec("survives"))
    # the bad interceptor is skipped; the record still flows
    assert [r.name for r in mem.records] == ["survives"]


def test_one_failing_exporter_does_not_affect_others() -> None:
    rt = reset_runtime()
    good = InMemoryExporter()

    class Raises:
        def export(self, records: Sequence[Record]) -> ExportResult:
            raise RuntimeError("backend down")

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    rt.add_exporter(Raises())
    rt.add_exporter(good)
    rt.emit_record(_rec("x"))
    assert [r.name for r in good.records] == ["x"]
    assert rt.export_failures == 1


def test_export_failure_result_is_counted() -> None:
    rt = reset_runtime()

    class Fails:
        def export(self, records: Sequence[Record]) -> ExportResult:
            return ExportResult.FAILURE

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    rt.add_exporter(Fails())
    rt.emit_record(_rec())
    assert rt.export_failures == 1


def test_events_delivered_in_order_and_isolated() -> None:
    rt = reset_runtime()
    seen: list[str] = []

    class Bad:
        def on_event(self, event: LifecycleEvent) -> None:
            raise RuntimeError("listener boom")

    class Good:
        def on_event(self, event: LifecycleEvent) -> None:
            seen.append(event.type)

    rt.add_listener(Bad())
    rt.add_listener(Good())
    rt.emit_event(LifecycleEvent(type=EventType.RUN_STARTED, run_id="r", unix_nanos=1))
    assert seen == [EventType.RUN_STARTED]


def test_force_flush_and_shutdown_handle_raising_exporters() -> None:
    rt = reset_runtime()

    class Raises:
        def export(self, records: Sequence[Record]) -> ExportResult:
            return ExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            raise RuntimeError("x")

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            raise RuntimeError("y")

    class FlushFalse:
        def export(self, records: Sequence[Record]) -> ExportResult:
            return ExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return False

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    rt.add_exporter(Raises())
    rt.add_exporter(FlushFalse())
    assert rt.force_flush() is False  # both a raise and a False ⇒ overall False
    rt.shutdown()  # must not raise
