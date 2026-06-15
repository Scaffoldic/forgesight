"""Doubles for testing the Datadog exporter without a live DD Agent.

:class:`InMemoryDatadogSpanWriter` and :class:`InMemoryDatadogMetricSink` satisfy the
``DatadogSpanWriter`` / ``DatadogMetricSink`` protocols and record everything written, so a
test (or a consuming team's pipeline test) can assert the mapped spans, unified tags, and
the cost / token DD metrics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .exporter import DatadogSpan


class InMemoryDatadogSpanWriter:
    """Captures every mapped :class:`DatadogSpan` instead of writing to a DD Agent."""

    def __init__(self) -> None:
        self.spans: list[DatadogSpan] = []
        self.flushed = 0
        self.stopped = False

    def write(self, span: DatadogSpan) -> None:
        self.spans.append(span)

    def flush(self) -> bool:
        self.flushed += 1
        return True

    def stop(self) -> None:
        self.stopped = True

    def by_resource(self) -> dict[str, DatadogSpan]:
        return {span.resource: span for span in self.spans}


@dataclass(frozen=True)
class MetricCall:
    """One emitted DD metric."""

    name: str
    value: float
    tags: list[str] = field(default_factory=list)


class InMemoryDatadogMetricSink:
    """Captures every emitted DD metric instead of sending to dogstatsd."""

    def __init__(self) -> None:
        self.metrics: list[MetricCall] = []
        self.closed = False

    def emit(self, name: str, value: float, tags: Sequence[str]) -> None:
        self.metrics.append(MetricCall(name=name, value=value, tags=list(tags)))

    def close(self) -> None:
        self.closed = True

    def named(self, name: str) -> list[MetricCall]:
        return [m for m in self.metrics if m.name == name]


__all__ = [
    "InMemoryDatadogMetricSink",
    "InMemoryDatadogSpanWriter",
    "MetricCall",
]
