"""ForgeSight Datadog exporter — DD-native APM spans + cost metric (DD Agent or OTLP)."""

from __future__ import annotations

from .exporter import (
    COST_METRIC,
    DD_SITES,
    OTLP_NATIVE_BACKENDS,
    TOKENS_METRIC,
    DatadogExporter,
    DatadogMetricSink,
    DatadogSpan,
    DatadogSpanWriter,
    record_to_span,
)
from .testing import InMemoryDatadogMetricSink, InMemoryDatadogSpanWriter, MetricCall

__version__ = "0.1.0"

__all__ = [
    "COST_METRIC",
    "DD_SITES",
    "OTLP_NATIVE_BACKENDS",
    "TOKENS_METRIC",
    "DatadogExporter",
    "DatadogMetricSink",
    "DatadogSpan",
    "DatadogSpanWriter",
    "InMemoryDatadogMetricSink",
    "InMemoryDatadogSpanWriter",
    "MetricCall",
    "__version__",
    "record_to_span",
]
