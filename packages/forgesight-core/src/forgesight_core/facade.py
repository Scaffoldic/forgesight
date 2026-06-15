"""The ``telemetry`` facade and the zero-config ``configure()`` entry point.

``configure()`` here is the minimal bootstrap (feat-002): it resets the runtime,
applies a few settings, and registers a default ``ConsoleExporter`` when none is
given. feat-010 replaces it with full env/YAML resolution and entry-point exporter
loading — the call site (``forgesight.configure()``) stays the same.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry.sdk.metrics.export import MetricReader

from forgesight_api import EventListener, Interceptor, PricingProvider, TelemetryExporter

from .config import load_adapters, load_settings, resolve
from .cost import TablePricingProvider
from .exporters import ConsoleExporter
from .interceptors import ContentCaptureGate
from .metrics import MetricConfig, MetricsSubsystem
from .processor import Runtime, RuntimeConfig, get_runtime, reset_runtime
from .scope import RunScope, WorkflowScope, current_run_scope


def _first(*values: object) -> Any:
    """Return the first non-None value (used for file → env → kwargs precedence)."""
    for value in values:
        if value is not None:
            return value
    return None


def _component(group: str, item: object, configs: Mapping[str, object]) -> Any:
    """Resolve a config entry: a name (str), a ``{name, config}`` dict, or an instance."""
    if isinstance(item, str):
        cfg = configs.get(item)
        return resolve(group, item, cfg if isinstance(cfg, Mapping) else None)
    if isinstance(item, Mapping):
        block = item.get("config")
        return resolve(group, str(item.get("name")), block if isinstance(block, Mapping) else None)
    return item


def configure(
    *,
    service_name: str | None = None,
    capture_content: bool | None = None,
    default_tool_type: str | None = None,
    sample_rate: float | None = None,
    sync_export: bool | None = None,
    max_queue_size: int | None = None,
    max_export_batch_size: int | None = None,
    schedule_delay_millis: int | None = None,
    deliver_step_events: bool | None = None,
    stack_capture_depth: int | None = None,
    capture_stacktrace: bool | None = None,
    exporters: Sequence[str | TelemetryExporter] | None = None,
    interceptors: Sequence[str | Interceptor | dict[str, object]] | None = None,
    listeners: Sequence[str | EventListener | dict[str, object]] | None = None,
    pricing: str | PricingProvider | None = None,
    pricing_overrides: dict[str, dict[str, object]] | None = None,
    exporter_config: dict[str, dict[str, object]] | None = None,
    metrics: MetricConfig | None = None,
    metric_reader: MetricReader | None = None,
    config_file: str | None = None,
) -> Runtime:
    """Initialise the SDK (FR-12). Layered config: file → env → kwargs (last wins).

    Named integrations (``str``) resolve via the ``forgesight.<group>`` entry points;
    an unknown name fails fast with the matching ``*NotRegisteredError``. With no
    file/env/kwargs it routes to a ``ConsoleExporter`` + the vendored pricing table.
    """
    settings = load_settings(config_file)
    raw_batch = settings.get("batch")
    batch = raw_batch if isinstance(raw_batch, dict) else {}

    config = RuntimeConfig()
    config.service_name = _first(service_name, settings.get("service_name"), config.service_name)
    config.capture_content = _first(
        capture_content, settings.get("capture_content"), config.capture_content
    )
    config.default_tool_type = _first(default_tool_type, config.default_tool_type)
    config.sample_rate = _first(sample_rate, settings.get("sample_rate"), config.sample_rate)
    config.sync_export = _first(sync_export, config.sync_export)
    config.max_queue_size = _first(
        max_queue_size, batch.get("max_queue_size"), config.max_queue_size
    )
    config.max_export_batch_size = _first(
        max_export_batch_size, batch.get("max_export_batch_size"), config.max_export_batch_size
    )
    config.schedule_delay_millis = _first(
        schedule_delay_millis, batch.get("schedule_delay_millis"), config.schedule_delay_millis
    )
    config.deliver_step_events = _first(
        deliver_step_events, settings.get("deliver_step_events"), config.deliver_step_events
    )
    config.stack_capture_depth = _first(stack_capture_depth, config.stack_capture_depth)
    config.capture_stacktrace = _first(capture_stacktrace, config.capture_stacktrace)
    config.__post_init__()  # re-validate after applying overrides
    rt = reset_runtime(config)

    exporter_cfg = exporter_config or settings.get("exporter_config") or {}
    raw_exporters = exporters if exporters is not None else settings.get("exporters")
    if raw_exporters is None:
        rt.add_exporter(ConsoleExporter())
    else:
        for item in raw_exporters:
            rt.add_exporter(_component("exporters", item, exporter_cfg))

    # The content gate is always first so no later interceptor or exporter can see
    # content the operator didn't opt into (P7/ADR-0007).
    rt.add_interceptor(ContentCaptureGate(capture_content=config.capture_content))
    raw_interceptors = (
        interceptors if interceptors is not None else settings.get("interceptors") or ()
    )
    for item in raw_interceptors:
        rt.add_interceptor(_component("interceptors", item, {}))

    raw_listeners = listeners if listeners is not None else settings.get("listeners") or ()
    for item in raw_listeners:
        rt.add_listener(_component("listeners", item, {}))

    # Resolution order (cost-model §4.1): provider-supplied cost (set_cost) > a
    # caller-registered provider > the vendored table > None. Default to the table.
    raw_pricing = pricing if pricing is not None else settings.get("pricing")
    if raw_pricing is None:
        rt.set_pricing(TablePricingProvider.from_vendored(overrides=pricing_overrides))
    elif isinstance(raw_pricing, str):
        rt.set_pricing(_component("pricing", raw_pricing, {}))
    else:
        rt.set_pricing(raw_pricing)

    metric_config = metrics if metrics is not None else MetricConfig()
    if metric_config.enabled:
        rt.metrics = MetricsSubsystem(metric_config, metric_reader)

    # Framework adapters (feat-019): only those named in the `adapters:` config block, so the
    # SDK's own process is never silently auto-instrumented.
    for adapter in load_adapters(settings):
        rt.add_adapter(adapter)
    return rt


class Telemetry:
    """The instrumentation facade. A process-wide singleton exposed as ``telemetry``."""

    def agent_run(
        self,
        name: str,
        *,
        version: str | None = None,
        parent_run_id: str | None = None,
        context_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RunScope:
        """Open an agent-run scope (the root of a run's trace)."""
        return RunScope(
            get_runtime(),
            name=name,
            version=version,
            parent_run_id=parent_run_id,
            context_id=context_id,
            metadata=metadata,
        )

    def workflow_run(
        self, name: str, *, metadata: dict[str, object] | None = None
    ) -> WorkflowScope:
        """Open a workflow scope that parents one or more agent runs / steps."""
        return WorkflowScope(get_runtime(), name=name, metadata=metadata)

    def current_run(self) -> RunScope | None:
        """The active :class:`RunScope`, or ``None`` outside any run."""
        return current_run_scope()


telemetry = Telemetry()
