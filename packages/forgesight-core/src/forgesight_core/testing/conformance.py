"""Per-SPI conformance suites — the SPI prose contracts as executable tests (P10).

Each ``run_*_conformance`` takes a *factory* (so every case gets a fresh instance) and
drives the implementation through the invariants the SPI promises. They raise
``AssertionError`` on a violation, so they drop straight into a pytest test. Every
shipped *and* third-party implementation is expected to pass its suite (NFR-7).
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

from forgesight_api import (
    EventListener,
    EventType,
    ExportResult,
    FrameworkAdapter,
    Interceptor,
    Kind,
    LifecycleEvent,
    PricingProvider,
    Record,
    RunStatus,
    TelemetryExporter,
    TokenUsage,
)


def _sample_record(name: str = "op") -> Record:
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
        attributes=MappingProxyType({}),
    )


def run_exporter_conformance(factory: Callable[[], TelemetryExporter]) -> None:
    """export never raises (returns FAILURE), flush/shutdown idempotent, batch + post-shutdown."""
    exporter = factory()
    result = exporter.export([_sample_record("a"), _sample_record("b")])
    assert isinstance(result, ExportResult), "export() must return an ExportResult"
    assert exporter.export([]) in (ExportResult.SUCCESS, ExportResult.FAILURE)
    assert isinstance(exporter.force_flush(), bool), "force_flush() must return bool"
    assert isinstance(exporter.force_flush(), bool)  # idempotent
    exporter.shutdown()
    exporter.shutdown()  # idempotent, must not raise
    # export after shutdown must not raise
    assert isinstance(exporter.export([_sample_record("c")]), ExportResult)


def run_interceptor_conformance(factory: Callable[[], Interceptor]) -> None:
    """intercept returns Record|None, doesn't raise on a valid record, doesn't mutate input."""
    interceptor = factory()
    record = _sample_record("x")
    out = interceptor.intercept(record)
    assert out is None or isinstance(out, Record), "intercept() must return Record | None"
    # the input is a frozen Record — confirm it is unchanged (no in-place mutation)
    assert record.name == "x"
    assert record.attributes == {}
    # idempotent shape on replay
    out2 = interceptor.intercept(_sample_record("x"))
    assert out2 is None or isinstance(out2, Record)


def run_event_listener_conformance(factory: Callable[[], EventListener]) -> None:
    """on_event handles the full RUN_STARTED→RUN_COMPLETED sequence without raising."""
    listener = factory()
    for event_type in EventType:
        listener.on_event(
            LifecycleEvent(type=event_type, run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A", unix_nanos=1)
        )


def run_pricing_conformance(factory: Callable[[], PricingProvider]) -> None:
    """price never raises; unknown model → None (not 0.0, not raise); known → non-negative."""
    provider = factory()
    unknown = provider.price(
        "definitely-not-a-provider", "definitely-not-a-model", TokenUsage(input=10)
    )
    assert unknown is None, "unknown (provider, model) must return None, not a number or raise"
    zero = provider.price("definitely-not-a-provider", "definitely-not-a-model", TokenUsage())
    assert zero is None or zero >= 0.0


def run_adapter_conformance(factory: Callable[[], FrameworkAdapter]) -> None:
    """name set; instrument/uninstrument/is_instrumented idempotent and reversible (feat-019)."""
    adapter = factory()
    assert isinstance(adapter.name, str), "adapter.name must be a str"
    assert adapter.name, "adapter.name must be non-empty"
    assert adapter.is_instrumented() is False, "a fresh adapter must not be instrumented"

    adapter.instrument()
    assert adapter.is_instrumented() is True, "instrument() must flip is_instrumented()"
    adapter.instrument()  # idempotent — second call is a no-op, still instrumented
    assert adapter.is_instrumented() is True

    adapter.uninstrument()
    assert adapter.is_instrumented() is False, "uninstrument() must flip is_instrumented()"
    adapter.uninstrument()  # idempotent, must not raise
    assert adapter.is_instrumented() is False

    adapter.instrument()  # re-instrument after uninstrument works
    assert adapter.is_instrumented() is True
    adapter.uninstrument()


__all__ = [
    "run_adapter_conformance",
    "run_event_listener_conformance",
    "run_exporter_conformance",
    "run_interceptor_conformance",
    "run_pricing_conformance",
]
