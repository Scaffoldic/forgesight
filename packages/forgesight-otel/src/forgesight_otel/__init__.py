"""ForgeSight OpenTelemetry exporter — OTLP spans via the GenAI semantic conventions."""

from __future__ import annotations

from .exporter import OTelExporter
from .propagation import extract, inject
from .semconv import SEMCONV_COMMIT, SEMCONV_VERSION, SemConvMapper

__version__ = "0.1.0"

__all__ = [
    "SEMCONV_COMMIT",
    "SEMCONV_VERSION",
    "OTelExporter",
    "SemConvMapper",
    "__version__",
    "extract",
    "inject",
]
