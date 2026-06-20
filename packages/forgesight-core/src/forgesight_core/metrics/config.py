"""Metric subsystem configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_EXPORT_INTERVAL_MS = 10_000


@dataclass(slots=True)
class AttributionMetricsConfig:
    """Live attributed-cost metric (feat-026). Off until a team opts in (P2).

    When enabled, the subsystem emits ``forgesight.cost.attributed_usd`` keyed by the
    stamped business-metadata ``dimensions`` (e.g. ``team`` / ``owner``) — the live
    counterpart of feat-022's offline chargeback rollup.
    """

    enabled: bool = False
    dimensions: tuple[str, ...] = ("team", "owner")
    unattributed_label: str = "<unattributed>"

    def __post_init__(self) -> None:
        if self.enabled and not self.dimensions:
            raise ValueError("attribution.cost_metrics.dimensions must be non-empty when enabled")


@dataclass(slots=True)
class MetricConfig:
    """Knobs for the metric subsystem (P8 — named, documented defaults)."""

    enabled: bool = True
    export_interval_millis: int = _DEFAULT_EXPORT_INTERVAL_MS
    enabled_instruments: frozenset[str] | None = None  # None ⇒ all
    attribution: AttributionMetricsConfig = field(default_factory=AttributionMetricsConfig)

    def __post_init__(self) -> None:
        if self.export_interval_millis <= 0:
            raise ValueError("export_interval_millis must be > 0")
