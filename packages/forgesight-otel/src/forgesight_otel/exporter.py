"""``OTelExporter`` — a ForgeSight ``TelemetryExporter`` that ships OTLP spans.

It runs on the export worker (feat-003), never the hot path. Each :class:`Record` is
turned into an OTel :class:`~opentelemetry.sdk.trace.ReadableSpan` (carrying ForgeSight's
own trace/span ids) and handed to an OTLP span exporter. ``export`` never raises (P6):
on any failure it returns ``ExportResult.FAILURE``.

For tests, inject a ``span_exporter`` (e.g. OTel's ``InMemorySpanExporter``); in
production the OTLP exporter is built lazily from ``endpoint``/``protocol``/``headers``.
"""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, TraceFlags
from opentelemetry.trace.status import Status, StatusCode

from forgesight_api import ExportResult, Record, RunStatus

from .semconv import FORGESIGHT_SEMCONV_VERSION, SEMCONV_VERSION, SemConvMapper

__version__ = "0.1.0"

_DEFAULT_SERVICE_NAME = "forgesight-agent"
_SAMPLED = TraceFlags(TraceFlags.SAMPLED)
_OK_STATUSES = frozenset({RunStatus.OK, RunStatus.RUNNING})


class OTelExporter:
    """Maps ForgeSight records → OTLP spans via the GenAI semantic conventions."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        protocol: str = "http/protobuf",
        service_name: str = _DEFAULT_SERVICE_NAME,
        capture_content: bool = False,
        emit_legacy_system: bool = False,
        headers: dict[str, str] | None = None,
        resource_attributes: dict[str, str] | None = None,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self._mapper = SemConvMapper()
        self._capture_content = capture_content
        self._emit_legacy_system = emit_legacy_system
        res: dict[str, str] = {
            "service.name": service_name,
            FORGESIGHT_SEMCONV_VERSION: SEMCONV_VERSION,
        }
        if resource_attributes:
            res.update(resource_attributes)
        self._resource = Resource.create(res)
        self._scope = InstrumentationScope("forgesight", __version__)
        self._span_exporter = span_exporter or self._build_span_exporter(
            endpoint, protocol, headers
        )

    # --- TelemetryExporter Protocol --------------------------------------
    def export(self, records: Sequence[Record]) -> ExportResult:
        try:
            spans = [self._to_readable_span(r) for r in records]
            result = self._span_exporter.export(spans)
        except Exception:  # defence in depth — export must never raise (P6)
            return ExportResult.FAILURE
        return ExportResult.SUCCESS if result is SpanExportResult.SUCCESS else ExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._span_exporter.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._span_exporter.shutdown()

    # --- internals --------------------------------------------------------
    def _to_readable_span(self, record: Record) -> ReadableSpan:
        trace_id = int(record.trace_id, 16)
        context = SpanContext(
            trace_id=trace_id,
            span_id=int(record.span_id, 16),
            is_remote=False,
            trace_flags=_SAMPLED,
        )
        parent = None
        if record.parent_span_id is not None:
            parent = SpanContext(
                trace_id=trace_id,
                span_id=int(record.parent_span_id, 16),
                is_remote=False,
                trace_flags=_SAMPLED,
            )
        attributes = self._mapper.attributes(
            record,
            capture_content=self._capture_content,
            emit_legacy_system=self._emit_legacy_system,
        )
        return ReadableSpan(
            name=self._mapper.span_name(record),
            context=context,
            parent=parent,
            resource=self._resource,
            attributes=attributes,
            kind=self._mapper.span_kind(record),
            status=_status(record.status),
            start_time=record.start_unix_nanos,
            end_time=record.end_unix_nanos,
            instrumentation_scope=self._scope,
        )

    @staticmethod
    def _build_span_exporter(
        endpoint: str | None, protocol: str, headers: dict[str, str] | None
    ) -> SpanExporter:
        if protocol in ("http", "http/protobuf"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            return OTLPSpanExporter(endpoint=endpoint, headers=headers)
        if protocol == "grpc":  # pragma: no cover - optional [grpc] extra
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter as GrpcExporter,
            )

            return GrpcExporter(endpoint=endpoint, headers=headers)  # type: ignore[no-any-return]
        raise ValueError(f"unknown protocol {protocol!r}; expected 'grpc' or 'http/protobuf'")


def _status(status: RunStatus) -> Status:
    if status is RunStatus.OK:
        return Status(StatusCode.OK)
    if status in _OK_STATUSES:  # RUNNING (shouldn't reach export) ⇒ unset
        return Status(StatusCode.UNSET)
    return Status(StatusCode.ERROR, description=status.value)
