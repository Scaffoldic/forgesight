"""Build sinks and the audit listener from config, and the ``install()`` convention.

Wiring paths (no core change needed — the audit tap is an ``EventListener``):

- ``configure(listeners=["audit"])`` resolves :func:`make_audit_listener` via the
  ``forgesight.listeners`` entry point.
- ``configure(...)`` then ``forgesight_audit.install({...})`` adds the listener to the runtime.
- Explicit: ``configure(listeners=[AuditListener(JsonlAuditSink("audit.jsonl"))])``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from typing import Any

from .listener import AuditListener
from .model import AuditKind
from .sink import AuditSink
from .sinks import JsonlAuditSink, OtelAuditSink, SiemAuditSink, SqliteAuditSink

_LISTENER_KEYS = frozenset(
    {"sink", "path", "endpoint", "kinds", "capture", "redact", "capture_content", "hash_algorithm"}
)


def build_sink(
    *,
    sink: str = "jsonl",
    path: str | None = None,
    endpoint: str | None = None,
    hash_algorithm: str = "sha256",
) -> AuditSink:
    """Resolve an audit-sink driver by name. Built-ins: jsonl, sqlite, otel, siem; custom
    drivers resolve from the ``forgesight.audit_sinks`` entry-point group."""
    if sink == "jsonl":
        if not path:
            raise ValueError("audit sink 'jsonl' requires a 'path'")
        return JsonlAuditSink(path, algorithm=hash_algorithm)
    if sink == "sqlite":
        if not path:
            raise ValueError("audit sink 'sqlite' requires a 'path'")
        return SqliteAuditSink(path, algorithm=hash_algorithm)
    if sink == "otel":
        return OtelAuditSink(algorithm=hash_algorithm)
    if sink == "siem":
        if not endpoint:
            raise ValueError("audit sink 'siem' requires an 'endpoint'")
        return SiemAuditSink(endpoint=endpoint, algorithm=hash_algorithm)
    factory = _load_sink_entry_point(sink)
    if factory is None:
        raise ValueError(f"unknown audit sink {sink!r}")
    return factory(algorithm=hash_algorithm)  # pragma: no cover - third-party driver edge


def _load_sink_entry_point(name: str) -> Callable[..., AuditSink] | None:  # pragma: no cover
    try:
        points = metadata.entry_points(group="forgesight.audit_sinks")
    except Exception:
        return None
    for point in points:
        if point.name == name and name not in ("jsonl", "sqlite", "otel", "siem"):
            loaded: Callable[..., AuditSink] = point.load()
            return loaded
    return None


def _parse_kinds(kinds: Sequence[str] | None) -> tuple[AuditKind, ...] | None:
    if kinds is None:
        return None
    return tuple(AuditKind(k) for k in kinds)


def make_audit_listener(
    *,
    sink: str = "jsonl",
    path: str | None = None,
    endpoint: str | None = None,
    kinds: Sequence[str] | None = None,
    capture: Mapping[str, Any] | None = None,
    redact: bool = True,
    capture_content: bool = False,
    hash_algorithm: str = "sha256",
    **_ignored: Any,
) -> AuditListener:
    """Factory for ``configure(listeners=["audit"])`` and ``install()``. Accepts the flat
    ``kinds`` list or the nested ``capture: {kinds: [...]}`` form."""
    if kinds is None and isinstance(capture, Mapping):
        raw = capture.get("kinds")
        kinds = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, str) else None
    sink_obj = build_sink(sink=sink, path=path, endpoint=endpoint, hash_algorithm=hash_algorithm)
    return AuditListener(
        sink_obj,
        kinds=_parse_kinds(kinds),
        redact=redact,
        capture_content=capture_content,
    )


def install(config: Mapping[str, Any] | None = None) -> AuditListener:
    """Build the audit listener from ``config`` and attach it to the active runtime.

    Call AFTER ``configure(...)`` (which builds a fresh runtime). Returns the listener."""
    from forgesight_core import get_runtime

    settings = {k: v for k, v in dict(config or {}).items() if k in _LISTENER_KEYS}
    listener = make_audit_listener(**settings)
    get_runtime().add_listener(listener)
    return listener
