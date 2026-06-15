"""W3C ``traceparent`` extraction from ASGI request headers.

A FastAPI service is a hop in a distributed trace: when a gateway or upstream already
started a trace and sent ``traceparent``, the agent run must continue it (be a child),
not open a disconnected root. This tiny pure module is the one place that parses the
incoming header — malformed input degrades to ``None`` (a new local root), never raises.
"""

from __future__ import annotations

from collections.abc import Iterable

_TRACEPARENT = b"traceparent"


def extract_parent(headers: Iterable[tuple[bytes, bytes]]) -> tuple[str, str] | None:
    """Return ``(trace_id, span_id)`` from the request's ``traceparent``, or ``None``."""
    for name, value in headers:
        if name.lower() == _TRACEPARENT:
            return _parse(value.decode("latin-1"))
    return None


def _parse(raw: str) -> tuple[str, str] | None:
    parts = raw.split("-")
    if len(parts) != 4:
        return None
    _version, trace_id, span_id, _flags = parts
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if not _is_hex(trace_id) or not _is_hex(span_id):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
