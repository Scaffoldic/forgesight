"""The dispatch runtime: interceptor chain → exporters, and events → listeners.

This is the central singleton the scopes hand records and events to. In feat-002 the
dispatch is **synchronous and fault-isolated** (each exporter / interceptor / listener
call is guarded so one failure never affects the agent or the others — P6). feat-003
replaces the synchronous fan-out with the async, bounded, batched export pipeline;
feat-010 populates this from configuration. The scope-facing surface
(:meth:`Runtime.emit_record` / :meth:`Runtime.emit_event`) stays the same across both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from forgesight_api import (
    EventListener,
    ExportResult,
    Interceptor,
    LifecycleEvent,
    PricingProvider,
    Record,
    TelemetryExporter,
)

_log = logging.getLogger("forgesight.runtime")

_DEFAULT_SERVICE_NAME = "forgesight-agent"
_DEFAULT_TOOL_TYPE = "function"


@dataclass(slots=True)
class RuntimeConfig:
    """Resolved runtime settings (feat-010 fills these from env/YAML/kwargs)."""

    service_name: str = _DEFAULT_SERVICE_NAME
    capture_content: bool = False
    default_tool_type: str = _DEFAULT_TOOL_TYPE


@dataclass(slots=True)
class Runtime:
    """Holds the registered SPI implementations and dispatches to them."""

    config: RuntimeConfig = field(default_factory=RuntimeConfig)
    exporters: list[TelemetryExporter] = field(default_factory=list)
    interceptors: list[Interceptor] = field(default_factory=list)
    listeners: list[EventListener] = field(default_factory=list)
    pricing: PricingProvider | None = None
    dropped: int = 0  # records dropped by an interceptor (vetoed); surfaced as a metric in feat-005
    export_failures: int = 0

    # --- registration -----------------------------------------------------
    def add_exporter(self, exporter: TelemetryExporter) -> None:
        self.exporters.append(exporter)

    def add_interceptor(self, interceptor: Interceptor) -> None:
        self.interceptors.append(interceptor)

    def add_listener(self, listener: EventListener) -> None:
        self.listeners.append(listener)

    def set_pricing(self, pricing: PricingProvider | None) -> None:
        self.pricing = pricing

    # --- dispatch ---------------------------------------------------------
    def emit_record(self, record: Record) -> None:
        """Run the interceptor chain, then fan out to every exporter (isolated)."""
        processed = self._run_interceptors(record)
        if processed is None:
            self.dropped += 1
            return
        for exporter in self.exporters:
            self._safe_export(exporter, processed)

    def emit_event(self, event: LifecycleEvent) -> None:
        """Deliver a lifecycle event to every listener in registration order (isolated)."""
        for listener in self.listeners:
            try:
                listener.on_event(event)
            except Exception:
                _log.exception("event listener %r raised on %s", listener, event.type)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        ok = True
        for exporter in self.exporters:
            try:
                ok = exporter.force_flush(timeout_millis) and ok
            except Exception:
                _log.exception("exporter %r raised during force_flush", exporter)
                ok = False
        return ok

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        for exporter in self.exporters:
            try:
                exporter.shutdown(timeout_millis)
            except Exception:
                _log.exception("exporter %r raised during shutdown", exporter)

    # --- internals --------------------------------------------------------
    def _run_interceptors(self, record: Record) -> Record | None:
        current: Record | None = record
        for interceptor in self.interceptors:
            if current is None:
                return None
            try:
                current = interceptor.intercept(current)
            except Exception:
                _log.exception("interceptor %r raised; skipping it", interceptor)
        return current

    def _safe_export(self, exporter: TelemetryExporter, record: Record) -> None:
        try:
            result = exporter.export([record])
        except Exception:
            self.export_failures += 1
            _log.exception("exporter %r raised during export", exporter)
            return
        if result is ExportResult.FAILURE:
            self.export_failures += 1
            _log.warning("exporter %r returned FAILURE", exporter)


_RUNTIME = Runtime()


def get_runtime() -> Runtime:
    """Return the process-wide :class:`Runtime` singleton."""
    return _RUNTIME


def reset_runtime() -> Runtime:
    """Reset the singleton to a fresh, empty state. For tests and re-``configure()``."""
    global _RUNTIME
    _RUNTIME = Runtime()
    return _RUNTIME
