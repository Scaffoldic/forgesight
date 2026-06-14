"""Metric subsystem configuration."""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_EXPORT_INTERVAL_MS = 10_000


@dataclass(slots=True)
class MetricConfig:
    """Knobs for the metric subsystem (P8 — named, documented defaults)."""

    enabled: bool = True
    export_interval_millis: int = _DEFAULT_EXPORT_INTERVAL_MS
    enabled_instruments: frozenset[str] | None = None  # None ⇒ all

    def __post_init__(self) -> None:
        if self.export_interval_millis <= 0:
            raise ValueError("export_interval_millis must be > 0")
