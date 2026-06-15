"""W3C trace-context propagation over the MCP request ``_meta`` carrier.

The MCP request ``_meta`` map is the carrier for ``traceparent`` / ``tracestate`` (per
current OTel MCP guidance). Keeping the carrier logic in one tiny, pure module means a
single place to re-pin if the carrier moves. The client injects on the way out; the server
extracts on the way in so its span continues the caller's trace.
"""

from __future__ import annotations

from collections.abc import Mapping

TRACEPARENT = "traceparent"
TRACESTATE = "tracestate"
_VERSION = "00"
_SAMPLED = "01"
_UNSAMPLED = "00"


def inject_traceparent(
    meta: Mapping[str, object] | None,
    *,
    trace_id: str,
    span_id: str,
    sampled: bool = True,
    tracestate: str | None = None,
) -> dict[str, object]:
    """Return a copy of ``meta`` with a W3C ``traceparent`` (and optional ``tracestate``)."""
    out: dict[str, object] = dict(meta) if meta else {}
    flags = _SAMPLED if sampled else _UNSAMPLED
    out[TRACEPARENT] = f"{_VERSION}-{trace_id}-{span_id}-{flags}"
    existing = out.get(TRACESTATE)
    if tracestate is not None:
        out[TRACESTATE] = tracestate
    elif not isinstance(existing, str):
        out.pop(TRACESTATE, None)
    return out


def extract_context(meta: Mapping[str, object] | None) -> tuple[str, str] | None:
    """Parse ``(trace_id, span_id)`` from a carrier's ``traceparent``, or ``None``.

    Returns ``None`` on a missing / malformed header (never raises) so a bad upstream
    header degrades to a new local trace rather than breaking the call.
    """
    if not meta:
        return None
    raw = meta.get(TRACEPARENT)
    if not isinstance(raw, str):
        return None
    parts = raw.split("-")
    if len(parts) != 4:
        return None
    _, trace_id, span_id, _flags = parts
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if not _is_hex(trace_id) or not _is_hex(span_id):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id


def get_tracestate(meta: Mapping[str, object] | None) -> str | None:
    """Return the incoming ``tracestate`` string if present, for onward propagation."""
    if not meta:
        return None
    value = meta.get(TRACESTATE)
    return value if isinstance(value, str) else None


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
