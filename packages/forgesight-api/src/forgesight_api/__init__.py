"""ForgeSight contracts — the locked telemetry domain model + SPIs.

This is the leaf of the dependency graph: it imports nothing from ``forgesight_core``
or any integration package, and depends on no backend or model-provider SDK
(stdlib + ``typing-extensions`` only). AgentForge and third-party agents depend on
this package to stay free of vendor lock-in.

See ``docs/features/feat-001-core-domain-model-and-contracts.md``.
"""

from __future__ import annotations

from .errors import (
    EventListenerNotRegisteredError,
    ExporterNotRegisteredError,
    InterceptorNotRegisteredError,
    PricingProviderNotRegisteredError,
)
from .ids import is_valid_trace_id, is_valid_ulid, new_trace_id, new_ulid
from .model import (
    AgentRun,
    Content,
    ErrorInfo,
    Kind,
    LLMCall,
    MCPCall,
    RunStatus,
    Step,
    TokenUsage,
    ToolCall,
    WorkflowRun,
)
from .record import EventType, ExportResult, LifecycleEvent, Record
from .spi import EventListener, Interceptor, PricingProvider, TelemetryExporter

__version__ = "0.1.0"

__all__ = [
    "AgentRun",
    "Content",
    "ErrorInfo",
    "EventListener",
    "EventListenerNotRegisteredError",
    "EventType",
    "ExportResult",
    "ExporterNotRegisteredError",
    "Interceptor",
    "InterceptorNotRegisteredError",
    "Kind",
    "LLMCall",
    "LifecycleEvent",
    "MCPCall",
    "PricingProvider",
    "PricingProviderNotRegisteredError",
    # exporter-facing values
    "Record",
    # enums
    "RunStatus",
    "Step",
    # SPIs
    "TelemetryExporter",
    # value / operation models
    "TokenUsage",
    "ToolCall",
    "WorkflowRun",
    "__version__",
    "is_valid_trace_id",
    "is_valid_ulid",
    "new_trace_id",
    # ids
    "new_ulid",
]
