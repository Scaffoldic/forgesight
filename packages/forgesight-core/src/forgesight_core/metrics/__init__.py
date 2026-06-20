"""ForgeSight metrics — FR-6 product metrics + the OTel GenAI histograms.

Derived automatically from the runtime's record stream; the agent author emits nothing.
"""

from __future__ import annotations

from .config import AttributionMetricsConfig, MetricConfig
from .instruments import KNOWN_INSTRUMENTS, MetricsSubsystem

__all__ = ["KNOWN_INSTRUMENTS", "AttributionMetricsConfig", "MetricConfig", "MetricsSubsystem"]
