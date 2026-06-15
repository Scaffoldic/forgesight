"""``DatadogExporter`` — ForgeSight records → Datadog APM spans + DD metrics.

A :class:`~forgesight_api.TelemetryExporter` (so it resolves via the
``forgesight.exporters`` entry point and passes the conformance suite) that surfaces
agent telemetry in Datadog with the unified ``service`` / ``env`` / ``version`` tags, the
SDK's computed cost as the monitorable DD metric ``forgesight.cost_usd``, and LLM / tool /
MCP calls as child APM spans.

Two transports:

* ``"agent"`` (default) maps each record to a :class:`DatadogSpan` and hands it to a
  :class:`DatadogSpanWriter` (a ``ddtrace`` writer to a local DD Agent by default), plus
  emits cost/token DD metrics via a :class:`DatadogMetricSink`. The vendor-backed default
  writer/sink are built lazily; tests inject doubles.
* ``"otlp"`` reuses ``forgesight-otel``'s :class:`~forgesight_otel.OTelExporter` pointed at
  the DD Agent's OTLP port (or DD's OTLP intake), with the DD unified tags applied as
  resource attributes Datadog reads.

``export`` never raises (P6): a DD Agent / intake outage returns ``ExportResult.FAILURE``,
counted by the pipeline, invisible to the agent. Content is attached only when
``capture_content`` is on (P7). Runs on the export worker, never the hot path.

**OTLP-native backends need no package.** Honeycomb / Jaeger / Tempo / SigNoz / New Relic /
X-Ray / Phoenix all ingest OTLP — point ``forgesight-otel`` at them (see
:data:`OTLP_NATIVE_BACKENDS`). Datadog earns a package only because its richest path is
DD-specific.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from forgesight_api import ExportResult, Kind, Record, RunStatus

_log = logging.getLogger("forgesight.datadog")

DEFAULT_SERVICE = "agentforge"
DEFAULT_SITE = "datadoghq.com"
DEFAULT_DD_AGENT_OTLP = "http://localhost:4317"

# Datadog sites a team may point at (architecture.md §4.5).
DD_SITES = frozenset(
    {
        "datadoghq.com",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "datadoghq.eu",
        "ap1.datadoghq.com",
        "ddog-gov.com",
    }
)

# The keystone, stated once: these ingest OTLP and need NO dedicated package — point
# forgesight-otel at them. Datadog is the deliberate exception (its richest path is
# DD-specific), which is why this package exists.
OTLP_NATIVE_BACKENDS: Mapping[str, str] = MappingProxyType(
    {
        "honeycomb": "forgesight-otel -> api.honeycomb.io:443 + x-honeycomb-team header",
        "jaeger": "forgesight-otel -> Jaeger OTLP :4317",
        "tempo": "forgesight-otel -> Grafana Tempo OTLP endpoint",
        "signoz": "forgesight-otel -> SigNoz OTLP collector",
        "newrelic": "forgesight-otel -> otlp.nr-data.net:4317 + api-key header",
        "xray": "forgesight-otel -> AWS Distro for OpenTelemetry (ADOT) collector",
        "phoenix": "forgesight-otel -> Arize Phoenix OTLP endpoint",
    }
)

_OP_INVOKE_AGENT = "invoke_agent"
_OP_INVOKE_WORKFLOW = "invoke_workflow"
_OP_CHAT = "chat"
_OP_EXECUTE_TOOL = "execute_tool"
_MCP_TOOLS_CALL = "tools/call"

_AGENT_VERSION_KEY = "agent.version"
_PARENT_RUN_ID_KEY = "parent.run_id"
_CONTEXT_ID_KEY = "context.id"
_STRUCTURED_KEYS = frozenset({_AGENT_VERSION_KEY, _PARENT_RUN_ID_KEY, _CONTEXT_ID_KEY})

_OK_STATUSES = frozenset({RunStatus.OK, RunStatus.RUNNING})

# DD APM operation names per kind (span.name; the detail goes in span.resource).
_DD_SPAN_NAME: Mapping[Kind, str] = {
    Kind.AGENT: "forgesight.agent",
    Kind.WORKFLOW: "forgesight.workflow",
    Kind.STEP: "forgesight.step",
    Kind.LLM: "forgesight.llm",
    Kind.TOOL: "forgesight.tool",
    Kind.MCP: "forgesight.mcp",
}

COST_METRIC = "forgesight.cost_usd"
TOKENS_METRIC = "forgesight.tokens"


@dataclass(frozen=True)
class DatadogSpan:
    """A backend-neutral DD APM span — the seam between mapping and the ``ddtrace`` writer."""

    trace_id: str  # W3C hex trace id (the writer narrows to DD's id space)
    span_id: str
    parent_id: str | None
    name: str  # DD operation name
    resource: str  # DD resource (agent_name / model / tool / method)
    service: str
    start_ns: int
    duration_ns: int
    error: int  # 1 on a failed op, else 0
    meta: dict[str, str]  # string tags
    metrics: dict[str, float]  # numeric tags


@runtime_checkable
class DatadogSpanWriter(Protocol):
    """Submits mapped spans to Datadog (``ddtrace`` writer → DD Agent by default)."""

    def write(self, span: DatadogSpan) -> None: ...

    def flush(self) -> bool: ...

    def stop(self) -> None: ...


@runtime_checkable
class DatadogMetricSink(Protocol):
    """Emits DD metrics (cost / tokens) — dogstatsd via the DD Agent by default."""

    def emit(self, name: str, value: float, tags: Sequence[str]) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class _Sink(Protocol):
    """The transport-specific delivery surface DatadogExporter delegates to."""

    def export(self, records: Sequence[Record]) -> ExportResult: ...

    def force_flush(self, timeout_millis: int) -> bool: ...

    def shutdown(self, timeout_millis: int) -> None: ...


def _env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class DatadogExporter:
    """Maps SDK records → Datadog APM spans + DD metrics (incl. cost). Stable from v0.2."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        site: str = DEFAULT_SITE,
        service: str = DEFAULT_SERVICE,
        env: str | None = None,
        version: str | None = None,
        agent_endpoint: str | None = None,
        transport: str = "agent",
        capture_content: bool = False,
        span_writer: DatadogSpanWriter | None = None,
        metric_sink: DatadogMetricSink | None = None,
        span_exporter: object | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else _env("DD_API_KEY", "FORGESIGHT_DATADOG_API_KEY")
        )
        self._site = (
            site
            if site != DEFAULT_SITE
            else (_env("DD_SITE", "FORGESIGHT_DATADOG_SITE") or DEFAULT_SITE)
        )
        if self._site not in DD_SITES:
            raise ValueError(
                f"unknown Datadog site {self._site!r}; expected one of {sorted(DD_SITES)}"
            )
        self._service = (
            service
            if service != DEFAULT_SERVICE
            else (_env("DD_SERVICE", "FORGESIGHT_DATADOG_SERVICE") or DEFAULT_SERVICE)
        )
        self._env = env if env is not None else _env("DD_ENV", "FORGESIGHT_DATADOG_ENV")
        self._version = (
            version if version is not None else _env("DD_VERSION", "FORGESIGHT_DATADOG_VERSION")
        )
        self._agent_endpoint = (
            agent_endpoint
            if agent_endpoint is not None
            else _env("FORGESIGHT_DATADOG_AGENT_ENDPOINT")
        )
        self._transport = (
            transport if transport != "agent" else (_env("FORGESIGHT_DATADOG_TRANSPORT") or "agent")
        )
        if self._transport not in ("agent", "otlp"):
            raise ValueError(f"transport must be 'agent' or 'otlp', got {self._transport!r}")
        self._capture_content = capture_content or _env_bool("FORGESIGHT_CAPTURE_CONTENT", False)

        self._sink: _Sink = self._build_sink(span_writer, metric_sink, span_exporter)

    # --- TelemetryExporter Protocol --------------------------------------
    def export(self, records: Sequence[Record]) -> ExportResult:
        return self._sink.export(records)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._sink.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._sink.shutdown(timeout_millis)

    # --- transport wiring -------------------------------------------------
    def _build_sink(
        self,
        span_writer: DatadogSpanWriter | None,
        metric_sink: DatadogMetricSink | None,
        span_exporter: object | None,
    ) -> _Sink:
        if self._transport == "otlp":
            if not self._agent_endpoint:
                raise ValueError(
                    "transport='otlp' requires agent_endpoint "
                    "(DD Agent OTLP port or DD OTLP intake)"
                )
            return _OTLPSink(
                endpoint=self._agent_endpoint,
                service=self._service,
                env=self._env,
                version=self._version,
                capture_content=self._capture_content,
                span_exporter=span_exporter,
            )
        # transport == "agent"
        if span_writer is None and not self._agent_endpoint and not self._api_key:
            raise ValueError(
                "transport='agent' direct intake requires api_key (or set agent_endpoint "
                "for a local DD Agent)"
            )
        writer = span_writer if span_writer is not None else self._default_span_writer()
        sink = metric_sink if metric_sink is not None else self._default_metric_sink()
        return _AgentSink(
            writer=writer,
            metric_sink=sink,
            service=self._service,
            env=self._env,
            version=self._version,
            capture_content=self._capture_content,
        )

    def _default_span_writer(self) -> DatadogSpanWriter:  # pragma: no cover - needs a live DD Agent
        from ._ddtrace import DDTraceSpanWriter

        return DDTraceSpanWriter(
            service=self._service,
            api_key=self._api_key,
            site=self._site,
            agent_endpoint=self._agent_endpoint,
        )

    def _default_metric_sink(self) -> DatadogMetricSink:  # pragma: no cover - needs a live DD Agent
        from ._ddtrace import DogStatsdMetricSink

        return DogStatsdMetricSink(agent_endpoint=self._agent_endpoint)


# --- record → DatadogSpan mapping (pure, fully tested) ----------------------
def _op(record: Record) -> str:
    kind = record.kind
    if kind is Kind.AGENT:
        return _OP_INVOKE_AGENT
    if kind is Kind.WORKFLOW:
        return _OP_INVOKE_WORKFLOW
    if kind is Kind.LLM:
        return _OP_CHAT
    if kind is Kind.TOOL:
        return _OP_EXECUTE_TOOL
    if kind is Kind.MCP and record.mcp is not None and record.mcp.method == _MCP_TOOLS_CALL:
        return _OP_EXECUTE_TOOL
    return ""


def _error_type(record: Record) -> str | None:
    if record.error is not None:
        return record.error.error_type
    if record.status not in _OK_STATUSES:
        return record.status.value
    return None


def _resource(record: Record) -> str:
    if record.kind is Kind.MCP and record.mcp is not None:
        return record.mcp.method
    return record.name


def record_to_span(
    record: Record, *, service: str, env: str | None, version: str | None, capture_content: bool
) -> DatadogSpan:
    """Map a Record onto a DD APM span with unified tags, gen_ai tags, and cost."""
    attrs = record.attributes
    meta: dict[str, str] = {"forgesight.run_id": record.run_id}
    if env is not None:
        meta["env"] = env
    if version is not None:
        meta["version"] = version
    for key, value in attrs.items():
        if key not in _STRUCTURED_KEYS:
            meta[key] = str(value)
    if _PARENT_RUN_ID_KEY in attrs:
        meta["forgesight.parent_run_id"] = str(attrs[_PARENT_RUN_ID_KEY])
    if _CONTEXT_ID_KEY in attrs:
        meta["gen_ai.conversation.id"] = str(attrs[_CONTEXT_ID_KEY])
    if _AGENT_VERSION_KEY in attrs:
        meta["gen_ai.agent.version"] = str(attrs[_AGENT_VERSION_KEY])

    op = _op(record)
    if op:
        meta["gen_ai.operation.name"] = op
    if record.kind is Kind.AGENT:
        meta["gen_ai.agent.name"] = record.name

    metrics: dict[str, float] = {}
    llm = record.llm
    if llm is not None:
        meta["gen_ai.provider.name"] = llm.provider
        meta["gen_ai.request.model"] = llm.request_model
        if llm.response_model is not None:
            meta["gen_ai.response.model"] = llm.response_model
        usage = llm.usage
        for tag, value in (
            ("input_tokens", usage.input),
            ("output_tokens", usage.output),
            ("cache_read_tokens", usage.cache_read),
            ("cache_creation_tokens", usage.cache_creation),
            ("reasoning_tokens", usage.reasoning),
        ):
            if value:
                metrics[f"gen_ai.usage.{tag}"] = float(value)
        if llm.cost_usd is not None:
            metrics[COST_METRIC] = llm.cost_usd
            meta[COST_METRIC] = f"{llm.cost_usd:.6f}"  # also a span tag (monitorable)
        if capture_content and llm.content is not None:
            _attach_content(llm.content, meta)
    if record.tool is not None:
        meta["gen_ai.tool.name"] = record.tool.name
        meta["gen_ai.tool.type"] = record.tool.tool_type
    if record.mcp is not None:
        meta["mcp.method.name"] = record.mcp.method
        meta["mcp.server"] = record.mcp.server
        if record.mcp.tool is not None:
            meta["gen_ai.tool.name"] = record.mcp.tool

    error_type = _error_type(record)
    if error_type is not None:
        meta["error.type"] = error_type
        if record.error is not None:
            meta["error.message"] = record.error.message

    end = record.end_unix_nanos if record.end_unix_nanos is not None else record.start_unix_nanos
    return DatadogSpan(
        trace_id=record.trace_id,
        span_id=record.span_id,
        parent_id=record.parent_span_id,
        name=_DD_SPAN_NAME[record.kind],
        resource=_resource(record),
        service=service,
        start_ns=record.start_unix_nanos,
        duration_ns=max(0, end - record.start_unix_nanos),
        error=0 if record.status in _OK_STATUSES else 1,
        meta=meta,
        metrics=metrics,
    )


def _attach_content(content: object, meta: dict[str, str]) -> None:
    import json

    for attr, key in (
        ("input_messages", "gen_ai.input.messages"),
        ("output_messages", "gen_ai.output.messages"),
        ("system_instructions", "gen_ai.system_instructions"),
    ):
        value = getattr(content, attr, None)
        if value is not None:
            meta[key] = json.dumps(value, default=str)


# --- transports -------------------------------------------------------------
class _AgentSink:
    """DD Agent transport: ddtrace span writer + dogstatsd cost/token metrics."""

    def __init__(
        self,
        *,
        writer: DatadogSpanWriter,
        metric_sink: DatadogMetricSink,
        service: str,
        env: str | None,
        version: str | None,
        capture_content: bool,
    ) -> None:
        self._writer = writer
        self._metrics = metric_sink
        self._service = service
        self._env = env
        self._version = version
        self._capture_content = capture_content

    def export(self, records: Sequence[Record]) -> ExportResult:
        try:
            for record in records:
                span = record_to_span(
                    record,
                    service=self._service,
                    env=self._env,
                    version=self._version,
                    capture_content=self._capture_content,
                )
                self._writer.write(span)
                self._emit_metrics(record, span)
        except Exception:  # a DD Agent outage is counted, never raised (P6)
            _log.warning("datadog agent export failed", exc_info=True)
            return ExportResult.FAILURE
        return ExportResult.SUCCESS

    def _emit_metrics(self, record: Record, span: DatadogSpan) -> None:
        llm = record.llm
        if llm is None:
            return
        base = [f"service:{self._service}"]
        if self._env is not None:
            base.append(f"env:{self._env}")
        model_tags = [*base, f"provider:{llm.provider}", f"model:{llm.request_model}"]
        if llm.cost_usd is not None:
            self._metrics.emit(COST_METRIC, llm.cost_usd, model_tags)
        for token_type, value in (
            ("input", llm.usage.input),
            ("output", llm.usage.output),
            ("cache_read", llm.usage.cache_read),
            ("cache_creation", llm.usage.cache_creation),
            ("reasoning", llm.usage.reasoning),
        ):
            if value:
                self._metrics.emit(
                    TOKENS_METRIC, float(value), [*model_tags, f"gen_ai_token_type:{token_type}"]
                )

    def force_flush(self, timeout_millis: int) -> bool:
        return self._writer.flush()

    def shutdown(self, timeout_millis: int) -> None:
        try:
            self._writer.stop()
        finally:
            self._metrics.close()


class _OTLPSink:
    """OTLP transport: forgesight-otel → DD Agent OTLP port, with DD unified tags."""

    def __init__(
        self,
        *,
        endpoint: str,
        service: str,
        env: str | None,
        version: str | None,
        capture_content: bool,
        span_exporter: object | None,
    ) -> None:
        from forgesight_otel import OTelExporter

        resource: dict[str, str] = {}
        if env is not None:
            resource["deployment.environment"] = env  # DD reads this as `env`
        if version is not None:
            resource["service.version"] = version  # DD reads this as `version`
        # http/protobuf needs no optional [grpc] extra; the DD Agent's OTLP/HTTP port is
        # :4318. (forgesight-otel can still do grpc if the extra is installed.)
        self._otel = OTelExporter(
            endpoint=endpoint,
            protocol="http/protobuf",
            service_name=service,
            capture_content=capture_content,
            resource_attributes=resource or None,
            span_exporter=span_exporter,  # type: ignore[arg-type]
        )

    def export(self, records: Sequence[Record]) -> ExportResult:
        return self._otel.export(records)

    def force_flush(self, timeout_millis: int) -> bool:
        return self._otel.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: int) -> None:
        self._otel.shutdown(timeout_millis)
