"""ForgeSight runtime — instrumentation scopes, context propagation, dispatch.

Most users import the ``forgesight`` facade rather than this package directly.
Adapter authors (feat-019) use the context primitives and scopes here.
"""

from __future__ import annotations

from .context import (
    TelemetryContext,
    current_context,
    new_run_id,
    new_span_id,
)
from .decorator import instrument
from .exporters import ConsoleExporter, InMemoryExporter
from .facade import Telemetry, configure, telemetry
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
    "ConsoleExporter",
    "InMemoryExporter",
    "LLMScope",
    "MCPScope",
    "RunScope",
    "Runtime",
    "RuntimeConfig",
    "StepScope",
    "Telemetry",
    "TelemetryContext",
    "ToolScope",
    "WorkflowScope",
    "__version__",
    "configure",
    "current_context",
    "current_run_scope",
    "get_runtime",
    "instrument",
    "new_run_id",
    "new_span_id",
    "reset_runtime",
    "telemetry",
]
