"""The four SPIs are runtime_checkable structural Protocols.

A plain class with the right methods IS an implementation (no inheritance); a class
missing a method fails ``isinstance``. This is the conformance seed feat-011 builds on.
"""

from __future__ import annotations

from collections.abc import Sequence

from forgesight_api import (
    EventListener,
    ExportResult,
    Interceptor,
    LifecycleEvent,
    PricingProvider,
    Record,
    TelemetryExporter,
    TokenUsage,
)


class _Exporter:
    def export(self, records: Sequence[Record]) -> ExportResult:
        return ExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        return None


class _Interceptor:
    def intercept(self, record: Record) -> Record | None:
        return record


class _Listener:
    def on_event(self, event: LifecycleEvent) -> None:
        return None


class _Pricer:
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        return usage.total * 1e-6


class _NotAnExporter:
    def export(self, records: Sequence[Record]) -> ExportResult:
        return ExportResult.SUCCESS

    # missing force_flush + shutdown


def test_structural_implementations_satisfy_their_protocol() -> None:
    assert isinstance(_Exporter(), TelemetryExporter)
    assert isinstance(_Interceptor(), Interceptor)
    assert isinstance(_Listener(), EventListener)
    assert isinstance(_Pricer(), PricingProvider)


def test_missing_method_fails_isinstance() -> None:
    assert not isinstance(_NotAnExporter(), TelemetryExporter)


def test_pricing_provider_returns_usd() -> None:
    pricer: PricingProvider = _Pricer()
    assert pricer.price("anthropic", "claude-sonnet-4-5", TokenUsage(input=1_000_000)) == 1.0


def test_interceptor_can_drop_by_returning_none() -> None:
    class _Dropper:
        def intercept(self, record: Record) -> Record | None:
            return None

    interceptor: Interceptor = _Dropper()
    rec = _Record_stub()
    assert interceptor.intercept(rec) is None


def _Record_stub() -> Record:
    from forgesight_api import Kind, RunStatus

    return Record(
        kind=Kind.STEP,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="step",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
