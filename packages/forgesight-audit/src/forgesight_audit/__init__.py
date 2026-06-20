"""ForgeSight audit trail — a tamper-evident, complete-capture projection of telemetry.

A second projection alongside the exporters: an append-only, hash-chained audit record with
integrity (``verify()``), complete capture past head-sampling (it rides the event bus), and a
compliance query/export surface. Opt-in; wire it as a listener::

    import forgesight
    from forgesight_audit import AuditListener, JsonlAuditSink

    forgesight.configure(listeners=[AuditListener(JsonlAuditSink("audit/agent-audit.jsonl"))])
"""

from __future__ import annotations

from .chain import canonical_bytes, compute_hash
from .config import build_sink, install, make_audit_listener
from .listener import AuditListener
from .model import DEFAULT_KINDS, AuditEvent, AuditKind, AuditQuery, VerifyResult
from .sink import AuditReport, AuditSink, verify
from .sinks import JsonlAuditSink, OtelAuditSink, SiemAuditSink, SqliteAuditSink

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_KINDS",
    # model
    "AuditEvent",
    "AuditKind",
    # wiring
    "AuditListener",
    "AuditQuery",
    "AuditReport",
    # sink surface
    "AuditSink",
    # drivers
    "JsonlAuditSink",
    "OtelAuditSink",
    "SiemAuditSink",
    "SqliteAuditSink",
    "VerifyResult",
    "__version__",
    "build_sink",
    "canonical_bytes",
    "compute_hash",
    "install",
    "make_audit_listener",
    "verify",
]
