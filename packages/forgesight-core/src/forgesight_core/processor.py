"""The dispatch runtime + the async export pipeline.

The hot path (``emit_record``) is non-blocking: it samples, runs the interceptor
chain, and enqueues into a **bounded** queue — no I/O, no awaiting an exporter
(P6, NFR-1/2). A single background worker drains the queue in batches and fans out
to every exporter, each call fault-isolated so one failing backend never affects the
agent or the others (NFR-3). Under sustained backpressure the queue drops the newest
record and counts it rather than growing unbounded (NFR-4).

``sync_export=True`` switches to inline, synchronous export — deterministic, used by
unit tests and simple scripts. The scope-facing surface (``emit_record`` /
``emit_event`` / ``force_flush`` / ``shutdown``) is identical in both modes.

See ``docs/design/exporter-pipeline.md``.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from forgesight_api import (
    EventListener,
    ExportResult,
    FrameworkAdapter,
    GovernanceSignal,
    Interceptor,
    LifecycleEvent,
    PricingProvider,
    Record,
    TelemetryExporter,
)

from .metrics import MetricsSubsystem

_log = logging.getLogger("forgesight.pipeline")

_DEFAULT_SERVICE_NAME = "forgesight-agent"
_DEFAULT_TOOL_TYPE = "function"
_DEFAULT_MAX_QUEUE = 2048
_DEFAULT_MAX_BATCH = 512
_DEFAULT_SCHEDULE_DELAY_MS = 5000
_DEFAULT_EXPORT_TIMEOUT_MS = 30000
_SAMPLE_DENOM = 1 << 64


@dataclass(slots=True)
class RuntimeConfig:
    """Resolved runtime + pipeline settings (feat-010 fills these from env/YAML)."""

    service_name: str = _DEFAULT_SERVICE_NAME
    capture_content: bool = False
    default_tool_type: str = _DEFAULT_TOOL_TYPE
    # pipeline knobs (P8 — named, documented defaults)
    max_queue_size: int = _DEFAULT_MAX_QUEUE
    max_export_batch_size: int = _DEFAULT_MAX_BATCH
    schedule_delay_millis: int = _DEFAULT_SCHEDULE_DELAY_MS
    export_timeout_millis: int = _DEFAULT_EXPORT_TIMEOUT_MS
    sample_rate: float = 1.0
    sync_export: bool = False  # inline export (deterministic) vs the async worker
    deliver_step_events: bool = True  # suppress STEP_* events on hot loops when False
    stack_capture_depth: int = 20  # frames formatted into ErrorInfo.stacktrace; 0 ⇒ none
    capture_stacktrace: bool = True  # capture the traceback on a failed operation

    def __post_init__(self) -> None:
        if self.max_export_batch_size > self.max_queue_size:
            raise ValueError("max_export_batch_size must not exceed max_queue_size")
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be in [0.0, 1.0]")


class Runtime:
    """Holds the registered SPI implementations and dispatches to them."""

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config if config is not None else RuntimeConfig()
        self.exporters: list[TelemetryExporter] = []
        self.interceptors: list[Interceptor] = []
        self.listeners: list[EventListener] = []
        self.pricing: PricingProvider | None = None
        self.dropped = 0  # records dropped by an interceptor veto OR a full queue (feat-005)
        self.export_failures = 0
        self.sampled_out = 0
        self.listener_errors = 0
        self.metrics: MetricsSubsystem | None = None
        self.adapters: list[FrameworkAdapter] = []  # framework adapters (feat-019)
        # Run-start metadata provider: (name, version) -> extra run-scoped metadata.
        # The registry (feat-022) stamps ownership through this; caller-set keys win.
        self.run_metadata_provider: Callable[[str, str | None], Mapping[str, str]] | None = None
        self._queue: queue.Queue[Record] = queue.Queue(maxsize=self.config.max_queue_size)
        self._export_lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._shutdown = False

    # --- registration -----------------------------------------------------
    def add_exporter(self, exporter: TelemetryExporter) -> None:
        self.exporters.append(exporter)

    def add_interceptor(self, interceptor: Interceptor) -> None:
        self.interceptors.append(interceptor)

    def add_listener(self, listener: EventListener) -> None:
        self.listeners.append(listener)

    def set_pricing(self, pricing: PricingProvider | None) -> None:
        self.pricing = pricing

    def add_adapter(self, adapter: FrameworkAdapter) -> None:
        """Track an instrumented framework adapter so shutdown can uninstrument it (feat-019)."""
        self.adapters.append(adapter)

    # --- dispatch (hot path) ---------------------------------------------
    def emit_record(self, record: Record) -> None:
        """Sample → interceptor chain → enqueue (or inline export in sync mode)."""
        if self.metrics is not None:
            self.metrics.record(record)  # metrics count all records, even unsampled traces
        if not self._sampled(record.trace_id):
            self.sampled_out += 1
            return
        processed = self._run_interceptors(record)
        if processed is None:
            self.dropped += 1
            return
        if self.config.sync_export:
            self._export_batch([processed])
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait(processed)
        except queue.Full:
            self.dropped += 1
            _log.warning("export queue full (size=%d); dropping record", self.config.max_queue_size)

    def emit_event(self, event: LifecycleEvent) -> None:
        """Deliver a lifecycle event to every listener in registration order (isolated)."""
        for listener in self.listeners:
            try:
                listener.on_event(event)
            except Exception:
                self.listener_errors += 1
                _log.exception("event listener %r raised on %s", listener, event.type)

    # --- flush / shutdown -------------------------------------------------
    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Drain the queue and flush every exporter. Blocking; idempotent; non-terminal."""
        self._drain()
        ok = True
        for exporter in self.exporters:
            try:
                ok = exporter.force_flush(timeout_millis) and ok
            except Exception:
                _log.exception("exporter %r raised during force_flush", exporter)
                ok = False
        return ok

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        """Stop the worker, drain, and shut down every exporter. Idempotent; terminal."""
        if self._shutdown:
            return
        self._shutdown = True
        for adapter in self.adapters:  # unsubscribe framework hooks (feat-019)
            try:
                adapter.uninstrument()
            except Exception:
                _log.exception("adapter %r raised during uninstrument", adapter)
        self.adapters.clear()
        self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout_millis / 1000)
        self._drain()
        for exporter in self.exporters:
            try:
                exporter.shutdown(timeout_millis)
            except Exception:
                _log.exception("exporter %r raised during shutdown", exporter)
        if self.metrics is not None:
            self.metrics.shutdown()

    # --- internals --------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._worker is not None or self._shutdown:
            return
        self._worker = threading.Thread(
            target=self._worker_loop, name="forgesight-export-worker", daemon=True
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        delay_s = self.config.schedule_delay_millis / 1000
        while not self._stop.is_set():
            if self._stop.wait(delay_s):
                break
            self._drain()
        self._drain()  # final drain after stop

    def _drain(self) -> None:
        batch_size = self.config.max_export_batch_size
        while True:
            batch: list[Record] = []
            for _ in range(batch_size):
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return
            self._export_batch(batch)

    def _export_batch(self, batch: Sequence[Record]) -> None:
        with self._export_lock:
            for exporter in self.exporters:
                self._safe_export(exporter, batch)

    def _run_interceptors(self, record: Record) -> Record | None:
        current: Record | None = record
        for interceptor in self.interceptors:
            if current is None:
                return None
            try:
                current = interceptor.intercept(current)
            except GovernanceSignal:
                raise  # a deliberate governance halt propagates by design (feat-020), not swallowed
            except Exception:
                _log.exception("interceptor %r raised; skipping it", interceptor)
        return current

    def _safe_export(self, exporter: TelemetryExporter, batch: Sequence[Record]) -> None:
        try:
            result = exporter.export(batch)
        except Exception:
            self.export_failures += 1
            _log.exception("exporter %r raised during export", exporter)
            return
        if result is ExportResult.FAILURE:
            self.export_failures += 1
            _log.warning("exporter %r returned FAILURE", exporter)

    def _sampled(self, trace_id: str) -> bool:
        rate = self.config.sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        try:
            bucket = int(trace_id[:16], 16)
        except ValueError:
            return True  # unparseable id ⇒ never silently drop
        return bucket / _SAMPLE_DENOM < rate


_RUNTIME = Runtime()


def get_runtime() -> Runtime:
    """Return the process-wide :class:`Runtime` singleton."""
    return _RUNTIME


def reset_runtime(config: RuntimeConfig | None = None) -> Runtime:
    """Shut the current runtime down and install a fresh one. For tests / re-``configure()``."""
    global _RUNTIME
    _RUNTIME.shutdown()
    _RUNTIME = Runtime(config)
    return _RUNTIME


def _atexit_shutdown() -> None:  # pragma: no cover - runs at interpreter exit
    _RUNTIME.shutdown()


atexit.register(_atexit_shutdown)
