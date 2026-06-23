"""``PrometheusExporter`` — folds ForgeSight records into a Prometheus registry.

A ``TelemetryExporter`` (so it resolves via the ``forgesight.exporters`` entry point and
passes the conformance suite) that derives the product metrics + GenAI histograms from
records into a ``prometheus_client`` registry, served on a pull ``/metrics`` endpoint
(and optionally pushed to a Pushgateway on flush/shutdown for short-lived runs).

Labels are cardinality-bounded by construction (fixed, low-cardinality label sets);
``run_id`` / ``trace_id`` are never labels — that's what traces are for.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    push_to_gateway,
    start_http_server,
)

from forgesight_api import ExportResult, Kind, Record, RunStatus
from forgesight_core.metrics.instruments import DURATION_BUCKETS, TOKEN_BUCKETS

_log = logging.getLogger("forgesight.prometheus")
_OK = frozenset({RunStatus.OK, RunStatus.RUNNING})
_NANOS_PER_S = 1_000_000_000


def _seconds(record: Record) -> float | None:
    if record.end_unix_nanos is None:
        return None
    return (record.end_unix_nanos - record.start_unix_nanos) / _NANOS_PER_S


class PrometheusExporter:
    """Bridge SDK metrics onto a Prometheus registry + pull endpoint / push-gateway."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 9464,
        prefix: str = "forgesight",
        push_gateway: str | None = None,
        push_job: str = "forgesight",
        registry: CollectorRegistry | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._push_gateway = push_gateway
        self._push_job = push_job
        self._registry = registry if registry is not None else CollectorRegistry()
        self._server: object | None = None
        self._server_started = False
        p = prefix
        reg = self._registry
        self._runs = Counter(
            f"{p}_agent_runs", "Agent runs", ["agent_name", "status"], registry=reg
        )
        self._failures = Counter(
            f"{p}_agent_failures", "Agent failures", ["agent_name", "error_type"], registry=reg
        )
        self._cost = Counter(
            f"{p}_agent_cost_usd", "Agent cost (USD)", ["gen_ai_provider_name"], registry=reg
        )
        self._agent_duration = Histogram(
            f"{p}_agent_duration_milliseconds",
            "Agent run duration (ms)",
            ["agent_name", "status"],
            registry=reg,
        )
        self._tool = Counter(
            f"{p}_tool_invocations", "Tool invocations", ["tool_name", "status"], registry=reg
        )
        self._mcp = Counter(
            f"{p}_mcp_invocations", "MCP invocations", ["mcp_method_name", "status"], registry=reg
        )
        self._tokens = Histogram(
            f"{p}_gen_ai_client_token_usage",
            "GenAI token usage",
            ["gen_ai_provider_name", "gen_ai_operation_name", "gen_ai_token_type"],
            buckets=TOKEN_BUCKETS,
            registry=reg,
        )
        self._op_duration = Histogram(
            f"{p}_gen_ai_client_operation_duration_seconds",
            "GenAI operation duration (s)",
            ["gen_ai_provider_name", "gen_ai_operation_name"],
            buckets=DURATION_BUCKETS,
            registry=reg,
        )

    # --- TelemetryExporter Protocol --------------------------------------
    def export(self, records: Sequence[Record]) -> ExportResult:
        try:
            for record in records:
                self._fold(record)
        except Exception:  # pragma: no cover - defensive; export must never raise (P6)
            _log.exception("prometheus fold failed")
            return ExportResult.FAILURE
        self._ensure_server()
        return ExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._push()

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._push()
        stop = getattr(self._server, "shutdown", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # pragma: no cover - best-effort
                _log.exception("prometheus server shutdown failed")

    # --- internals --------------------------------------------------------
    def _fold(self, record: Record) -> None:
        status = record.status.value
        if record.kind is Kind.AGENT:
            self._runs.labels(record.name, status).inc()
            seconds = _seconds(record)
            if seconds is not None:
                self._agent_duration.labels(record.name, status).observe(seconds * 1000.0)
            if record.status not in _OK:
                error_type = record.error.error_type if record.error else status
                self._failures.labels(record.name, error_type).inc()
        elif record.kind is Kind.LLM and record.llm is not None:
            self._fold_llm(record)
        elif record.kind is Kind.TOOL and record.tool is not None:
            self._tool.labels(record.tool.name, status).inc()
        elif record.kind is Kind.MCP and record.mcp is not None:
            self._mcp.labels(record.mcp.method, status).inc()

    def _fold_llm(self, record: Record) -> None:
        llm = record.llm
        assert llm is not None
        usage = llm.usage
        for token_type, value in (
            ("input", usage.input),
            ("output", usage.output),
            ("cache_read", usage.cache_read),
            ("cache_creation", usage.cache_creation),
            ("reasoning", usage.reasoning),
        ):
            if value:
                self._tokens.labels(llm.provider, "chat", token_type).observe(value)
        seconds = _seconds(record)
        if seconds is not None:
            self._op_duration.labels(llm.provider, "chat").observe(seconds)
        if llm.cost_usd is not None:
            self._cost.labels(llm.provider).inc(llm.cost_usd)

    def _ensure_server(self) -> None:
        if self._server_started or self._port == 0:
            return
        self._server_started = True
        try:
            result = start_http_server(self._port, addr=self._host, registry=self._registry)
            self._server = result[0] if isinstance(result, tuple) else None
        except OSError:  # pragma: no cover - port in use / bind failure is isolated
            _log.warning("prometheus /metrics server could not bind %s:%d", self._host, self._port)

    def _push(self) -> bool:
        if self._push_gateway is None:
            return True
        try:
            push_to_gateway(self._push_gateway, job=self._push_job, registry=self._registry)
        except Exception:
            _log.warning("prometheus push to %s failed", self._push_gateway)
            return False
        return True
