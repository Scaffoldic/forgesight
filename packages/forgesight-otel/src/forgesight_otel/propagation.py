"""W3C TraceContext propagation helpers for cross-process / cross-agent hops.

Used by A2A (feat-014) and MCP (feat-016) integrations to stitch one end-to-end trace
across processes: the caller injects ``traceparent``/``tracestate`` from a ForgeSight
trace/span id, the callee extracts them and opens its span as a child.
"""

from __future__ import annotations

from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    get_current_span,
    set_span_in_context,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROP = TraceContextTextMapPropagator()
_SAMPLED = TraceFlags(TraceFlags.SAMPLED)


def inject(trace_id: str, span_id: str, carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Inject ``traceparent``/``tracestate`` for the given ids into a carrier dict."""
    out: dict[str, str] = {} if carrier is None else carrier
    context = set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=int(trace_id, 16),
                span_id=int(span_id, 16),
                is_remote=False,
                trace_flags=_SAMPLED,
            )
        )
    )
    _PROP.inject(out, context=context)
    return out


def extract(carrier: dict[str, str]) -> tuple[str, str] | None:
    """Extract ``(trace_id_hex, span_id_hex)`` from a carrier, or ``None`` if absent."""
    span_context = get_current_span(_PROP.extract(carrier)).get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x"), format(span_context.span_id, "016x")
