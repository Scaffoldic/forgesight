"""The ``telemetry`` facade and the zero-config ``configure()`` entry point.

``configure()`` here is the minimal bootstrap (feat-002): it resets the runtime,
applies a few settings, and registers a default ``ConsoleExporter`` when none is
given. feat-010 replaces it with full env/YAML resolution and entry-point exporter
loading — the call site (``forgesight.configure()``) stays the same.
"""

from __future__ import annotations

from collections.abc import Sequence

from forgesight_api import EventListener, Interceptor, PricingProvider, TelemetryExporter

from .exporters import ConsoleExporter
from .processor import Runtime, get_runtime, reset_runtime
from .scope import RunScope, WorkflowScope, current_run_scope


def configure(
    *,
    service_name: str | None = None,
    capture_content: bool | None = None,
    default_tool_type: str | None = None,
    exporters: Sequence[TelemetryExporter] | None = None,
    interceptors: Sequence[Interceptor] | None = None,
    listeners: Sequence[EventListener] | None = None,
    pricing: PricingProvider | None = None,
) -> Runtime:
    """Initialise the SDK. With no arguments, routes to a ``ConsoleExporter`` (FR-12)."""
    rt = reset_runtime()
    if service_name is not None:
        rt.config.service_name = service_name
    if capture_content is not None:
        rt.config.capture_content = capture_content
    if default_tool_type is not None:
        rt.config.default_tool_type = default_tool_type
    for exporter in exporters if exporters is not None else [ConsoleExporter()]:
        rt.add_exporter(exporter)
    for interceptor in interceptors or ():
        rt.add_interceptor(interceptor)
    for listener in listeners or ():
        rt.add_listener(listener)
    rt.set_pricing(pricing)
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
