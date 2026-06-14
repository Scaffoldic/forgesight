"""Per-SPI conformance suites — re-exported from ``forgesight_core.testing.conformance``."""

from __future__ import annotations

from forgesight_core.testing.conformance import (
    run_event_listener_conformance,
    run_exporter_conformance,
    run_interceptor_conformance,
    run_pricing_conformance,
)

__all__ = [
    "run_event_listener_conformance",
    "run_exporter_conformance",
    "run_interceptor_conformance",
    "run_pricing_conformance",
]
