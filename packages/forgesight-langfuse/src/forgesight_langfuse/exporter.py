"""``LangfuseExporter`` — ForgeSight records → Langfuse via OTLP + ``langfuse.*`` attrs.

Wraps ``forgesight-otel``'s ``OTelExporter`` pointed at Langfuse's OTLP ingest endpoint
(``/api/public/otel``, HTTP, Basic auth) and enriches each record with the native
``langfuse.*`` attributes Langfuse reads (observation type; trace name / user / session /
tags). LLM calls land as ``generation`` observations, tools as ``tool`` observations,
steps as ``span`` — with the SDK's computed ``forgesight.usage.cost_usd`` ingested.

Content (prompts/completions) is captured only when ``capture_content`` is on (P7).
``export`` never raises (P6). Runs on the pipeline worker, never the hot path.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType

from opentelemetry.sdk.trace.export import SpanExporter

from forgesight_api import ExportResult, Kind, Record
from forgesight_otel import OTelExporter

_REGION_HOSTS = {
    "us": "https://us.cloud.langfuse.com",
    "eu": "https://cloud.langfuse.com",
}
_OBSERVATION_TYPE = {
    Kind.AGENT: "agent",
    Kind.WORKFLOW: "chain",
    Kind.STEP: "span",
    Kind.LLM: "generation",
    Kind.TOOL: "tool",
    Kind.MCP: "tool",
}
# business-metadata keys lifted to trace-level langfuse attributes (on the root span)
_TRACE_LIFTS = {
    "user_id": "langfuse.user.id",
    "session_id": "langfuse.session.id",
    "tags": "langfuse.trace.tags",
}

LANGFUSE_OBSERVATION_TYPE = "langfuse.observation.type"
LANGFUSE_TRACE_NAME = "langfuse.trace.name"


def basic_auth_header(public_key: str, secret_key: str) -> str:
    """Return the ``Basic base64(pk:sk)`` value Langfuse's OTLP endpoint expects."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return f"Basic {token}"


def otlp_traces_endpoint(host: str) -> str:
    """Langfuse's OTLP/HTTP traces URL: the ``/v1/traces`` signal under ``/api/public/otel``.
    The signal path is required — posting to the bare ``/api/public/otel`` base 404s."""
    return f"{host.rstrip('/')}/api/public/otel/v1/traces"


class LangfuseExporter:
    """Export records to Langfuse over OTLP with native ``langfuse.*`` enrichment."""

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str | None = None,
        region: str | None = None,
        capture_content: bool = False,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        if not public_key or not secret_key:
            raise ValueError("LangfuseExporter requires public_key and secret_key")
        resolved_host = host or _REGION_HOSTS.get((region or "").lower(), _REGION_HOSTS["eu"])
        self._host = resolved_host.rstrip("/")
        self._otel = OTelExporter(
            endpoint=otlp_traces_endpoint(self._host),
            protocol="http/protobuf",
            service_name="forgesight",
            capture_content=capture_content,
            headers={"Authorization": basic_auth_header(public_key, secret_key)},
            span_exporter=span_exporter,
        )

    # --- TelemetryExporter Protocol --------------------------------------
    def export(self, records: Sequence[Record]) -> ExportResult:
        return self._otel.export([self._enrich(r) for r in records])

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._otel.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._otel.shutdown(timeout_millis)

    # --- internals --------------------------------------------------------
    def _enrich(self, record: Record) -> Record:
        attrs: dict[str, object] = dict(record.attributes)
        attrs[LANGFUSE_OBSERVATION_TYPE] = _OBSERVATION_TYPE[record.kind]
        if record.parent_span_id is None and record.kind in (Kind.AGENT, Kind.WORKFLOW):
            attrs[LANGFUSE_TRACE_NAME] = record.name
            for meta_key, lf_key in _TRACE_LIFTS.items():
                if meta_key in attrs:
                    attrs[lf_key] = attrs[meta_key]
        return replace(record, attributes=MappingProxyType(attrs))
