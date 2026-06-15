"""ForgeSight runtime — instrumentation scopes, context propagation, dispatch.

Most users import the ``forgesight`` facade rather than this package directly.
Adapter authors (feat-019) use the context primitives and scopes here.
"""

from __future__ import annotations

from .adapters import BaseAdapter, ScopeBridge, in_tool_call, tool_call_active
from .config import register, resolve
from .context import (
    TelemetryContext,
    current_context,
    new_run_id,
    new_span_id,
)
from .cost import PricingTable, TablePricingProvider
from .decorator import instrument
from .exporters import ConsoleExporter, InMemoryExporter
from .facade import Telemetry, configure, telemetry
from .interceptors import ContentCaptureGate, PIIRedactionInterceptor
from .metrics import MetricConfig, MetricsSubsystem
from .processor import Runtime, RuntimeConfig, get_runtime, reset_runtime
from .scope import (
    LLMScope,
    MCPScope,
    RunScope,
    StepScope,
    ToolScope,
    WorkflowScope,
    current_run_scope,
)

__version__ = "0.1.0"

__all__ = [
    "BaseAdapter",
    "ConsoleExporter",
    "ContentCaptureGate",
    "InMemoryExporter",
    "LLMScope",
    "MCPScope",
    "MetricConfig",
    "MetricsSubsystem",
    "PIIRedactionInterceptor",
    "PricingTable",
    "RunScope",
    "Runtime",
    "RuntimeConfig",
    "ScopeBridge",
    "StepScope",
    "TablePricingProvider",
    "Telemetry",
    "TelemetryContext",
    "ToolScope",
    "WorkflowScope",
    "__version__",
    "configure",
    "current_context",
    "current_run_scope",
    "get_runtime",
    "in_tool_call",
    "instrument",
    "new_run_id",
    "new_span_id",
    "register",
    "reset_runtime",
    "resolve",
    "telemetry",
    "tool_call_active",
]
