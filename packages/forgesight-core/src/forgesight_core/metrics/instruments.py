"""The metric instruments + the derivation from records.

The ``MetricsSubsystem`` owns a (local, non-global) OTel ``MeterProvider`` with the
GenAI histograms' **exact** spec bucket boundaries (via Views), plus the SDK's own
``forgesight.*`` product instruments. The runtime feeds it the same ``Record`` stream
the trace pipeline sees, so metrics can never drift from spans (feat-005 §4.3).

Recording a point is in-memory aggregation — no network on the hot path (P6/NFR-2).
Transport (push OTLP / pull Prometheus) is the integration packages' job; this module
owns the instruments + a default in-memory reader.
"""

from __future__ import annotations

from opentelemetry.metrics import Counter, Histogram, Meter, _Gauge
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

from forgesight_api import Kind, Record, RunStatus

from .config import MetricConfig

# --- bucket boundaries (otel-semantic-conventions.md §4.4) -----------------
TOKEN_BUCKETS = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)
DURATION_BUCKETS = (
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
)
WORKFLOW_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200)

# --- instrument names ------------------------------------------------------
M_RUNS = "forgesight.agent.runs_total"
M_FAILURES = "forgesight.agent.failures_total"
M_COST = "forgesight.agent.cost_total"
M_AGENT_DURATION = "forgesight.agent.duration_ms"
M_TOOL = "forgesight.tool.invocations_total"
M_MCP = "forgesight.mcp.invocations_total"
M_TOKEN_USAGE = "gen_ai.client.token.usage"
M_OP_DURATION = "gen_ai.client.operation.duration"
M_TTFC = "gen_ai.client.operation.time_to_first_chunk"
M_WORKFLOW_DURATION = "gen_ai.workflow.duration"
M_MCP_DURATION = "mcp.client.operation.duration"
# feat-026 — live cost attribution. forgesight.* (never gen_ai.*: OTel defines no cost metric).
M_COST_ATTRIBUTED = "forgesight.cost.attributed_usd"
M_BUDGET_UTIL = "forgesight.cost.budget_utilization"

KNOWN_INSTRUMENTS = frozenset(
    {
        M_RUNS,
        M_FAILURES,
        M_COST,
        M_AGENT_DURATION,
        M_TOOL,
        M_MCP,
        M_TOKEN_USAGE,
        M_OP_DURATION,
        M_TTFC,
        M_WORKFLOW_DURATION,
        M_MCP_DURATION,
        M_COST_ATTRIBUTED,
        M_BUDGET_UTIL,
    }
)

_GENAI_VIEWS = {
    M_TOKEN_USAGE: TOKEN_BUCKETS,
    M_OP_DURATION: DURATION_BUCKETS,
    M_TTFC: DURATION_BUCKETS,
    M_WORKFLOW_DURATION: WORKFLOW_BUCKETS,
    M_MCP_DURATION: DURATION_BUCKETS,
}
_OK_STATUSES = frozenset({RunStatus.OK, RunStatus.RUNNING})
_NANOS_PER_S = 1_000_000_000


def _seconds(start: int, end: int | None) -> float | None:
    return None if end is None else (end - start) / _NANOS_PER_S


class MetricsSubsystem:
    """Builds the instruments and records metric points derived from records."""

    def __init__(self, config: MetricConfig, reader: MetricReader | None = None) -> None:
        enabled = config.enabled_instruments
        if enabled is not None:
            unknown = enabled - KNOWN_INSTRUMENTS
            if unknown:
                raise ValueError(f"unknown instruments: {sorted(unknown)}")
        self._enabled = enabled
        self._reader = reader if reader is not None else InMemoryMetricReader()
        views = [
            View(instrument_name=name, aggregation=ExplicitBucketHistogramAggregation(buckets))
            for name, buckets in _GENAI_VIEWS.items()
            if self._on(name)
        ]
        self._attribution = config.attribution
        self._provider = MeterProvider(metric_readers=[self._reader], views=views)
        meter = self._provider.get_meter("forgesight")
        self._counters: dict[str, Counter] = {}
        self._histos: dict[str, Histogram] = {}
        self._gauges: dict[str, _Gauge] = {}
        self._build(meter)

    @property
    def reader(self) -> MetricReader:
        return self._reader

    def collect(self) -> object:
        """Return the current aggregated metrics (InMemoryMetricReader only)."""
        if isinstance(self._reader, InMemoryMetricReader):
            return self._reader.get_metrics_data()
        return None  # pragma: no cover - push readers don't expose a pull snapshot

    def shutdown(self) -> None:
        self._provider.shutdown()

    # --- recording --------------------------------------------------------
    def record(self, record: Record) -> None:
        if record.kind is Kind.AGENT:
            self._record_agent(record)
        elif record.kind is Kind.WORKFLOW:
            self._hist(
                M_WORKFLOW_DURATION, _seconds(record.start_unix_nanos, record.end_unix_nanos), {}
            )
        elif record.kind is Kind.LLM and record.llm is not None:
            self._record_llm(record)
        elif record.kind is Kind.TOOL and record.tool is not None:
            self._add(
                M_TOOL,
                1,
                {
                    "gen_ai.tool.name": record.tool.name,
                    "gen_ai.tool.type": record.tool.tool_type,
                    "status": record.status.value,
                },
            )
        elif record.kind is Kind.MCP and record.mcp is not None:
            self._add(
                M_MCP, 1, {"mcp.method.name": record.mcp.method, "status": record.status.value}
            )
            self._hist(
                M_MCP_DURATION,
                _seconds(record.start_unix_nanos, record.end_unix_nanos),
                {"mcp.method.name": record.mcp.method},
            )

    def _record_agent(self, record: Record) -> None:
        status = record.status.value
        attrs: dict[str, str] = {"agent.name": record.name, "status": status}
        version = record.attributes.get("agent.version")
        if version is not None:
            attrs["agent.version"] = str(version)
        self._add(M_RUNS, 1, attrs)
        self._hist(
            M_AGENT_DURATION, record.duration_ms, {"agent.name": record.name, "status": status}
        )
        if record.status not in _OK_STATUSES:
            self._add(M_FAILURES, 1, {"agent.name": record.name, "error.type": status})

    def _record_llm(self, record: Record) -> None:
        llm = record.llm
        assert llm is not None
        base = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": llm.provider}
        usage = llm.usage
        for token_type, value in (
            ("input", usage.input),
            ("output", usage.output),
            ("cache_read", usage.cache_read),
            ("cache_creation", usage.cache_creation),
            ("reasoning", usage.reasoning),
        ):
            if value:
                self._hist(M_TOKEN_USAGE, value, {**base, "gen_ai.token.type": token_type})
        self._hist(M_OP_DURATION, _seconds(record.start_unix_nanos, record.end_unix_nanos), base)
        if llm.time_to_first_chunk_ms is not None:
            self._hist(M_TTFC, llm.time_to_first_chunk_ms / 1000.0, base)
        if llm.cost_usd is not None:
            self._add(M_COST, llm.cost_usd, {"gen_ai.provider.name": llm.provider})
            if self._attribution.enabled:
                attrs = {
                    dim: str(record.attributes.get(dim, self._attribution.unattributed_label))
                    for dim in self._attribution.dimensions
                }
                attrs["gen_ai.provider.name"] = llm.provider
                self._add(M_COST_ATTRIBUTED, llm.cost_usd, attrs)

    def set_budget_utilization(self, value: float, attrs: dict[str, str]) -> None:
        """Record the spend/cap ratio for a budget scope key (feat-026). Called by the
        governance ``BudgetInterceptor`` through the runtime — core stays vendor-neutral
        (the instrument is a generic gauge; the budget semantics live in governance)."""
        gauge = self._gauges.get(M_BUDGET_UTIL)
        if gauge is not None:
            gauge.set(value, attrs)

    # --- instrument helpers ----------------------------------------------
    def _on(self, name: str) -> bool:
        return self._enabled is None or name in self._enabled

    def _build(self, meter: Meter) -> None:
        counters = {
            M_RUNS: "{run}",
            M_FAILURES: "{run}",
            M_COST: "usd",
            M_TOOL: "{invocation}",
            M_MCP: "{invocation}",
            M_COST_ATTRIBUTED: "usd",
        }
        histos = {
            M_AGENT_DURATION: "ms",
            M_TOKEN_USAGE: "{token}",
            M_OP_DURATION: "s",
            M_TTFC: "s",
            M_WORKFLOW_DURATION: "s",
            M_MCP_DURATION: "s",
        }
        gauges = {M_BUDGET_UTIL: "1"}
        for name, unit in counters.items():
            if self._on(name):
                self._counters[name] = meter.create_counter(name, unit=unit)
        for name, unit in histos.items():
            if self._on(name):
                self._histos[name] = meter.create_histogram(name, unit=unit)
        for name, unit in gauges.items():
            if self._on(name):
                self._gauges[name] = meter.create_gauge(name, unit=unit)

    def _add(self, name: str, value: float, attrs: dict[str, str]) -> None:
        counter = self._counters.get(name)
        if counter is not None:
            counter.add(value, attrs)

    def _hist(self, name: str, value: float | None, attrs: dict[str, str]) -> None:
        if value is None:
            return
        histogram = self._histos.get(name)
        if histogram is not None:
            histogram.record(value, attrs)
