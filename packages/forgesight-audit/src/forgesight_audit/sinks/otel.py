"""Bridge driver: emit each ``AuditEvent`` as an OpenTelemetry log record.

The audit projection lands in the same backend as the traces (correlated by
``run_id``/``trace_id``) without inventing a parallel store (P4). Custom fields are
namespaced ``forgesight.audit.*`` — no invented semantic-convention attributes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..model import AuditEvent
from .base import _BridgeSink

#: A pluggable emit function — injected in tests; the default emits an OTel log record.
EmitFn = Callable[[AuditEvent], None]


class OtelAuditSink(_BridgeSink):
    """Hash-chains in process (for this-session query/verify) and emits each event as an
    OTel log record via ``emit`` (default: the OTel logs API)."""

    def __init__(self, *, emit: EmitFn | None = None, algorithm: str = "sha256") -> None:
        self._emit_fn: EmitFn = emit if emit is not None else _default_emit
        super().__init__(algorithm=algorithm)

    def _emit(self, event: AuditEvent) -> None:
        self._emit_fn(event)


def _default_emit(event: AuditEvent) -> None:  # pragma: no cover - needs a live OTel logs provider
    """Emit ``event`` as an OTel log record. Requires a configured ``LoggerProvider`` with a
    log exporter; raises (and is counted) if the logs API is unavailable."""
    from opentelemetry._logs import get_logger
    from opentelemetry.sdk._logs import LogRecord

    attributes: dict[str, Any] = {
        "forgesight.audit.kind": str(event.kind),
        "forgesight.audit.seq": event.seq,
        "forgesight.audit.hash": event.hash,
        "forgesight.audit.principal": event.principal,
        "run.id": event.run_id,
    }
    if event.cost_usd is not None:
        attributes["forgesight.usage.cost_usd"] = event.cost_usd
    logger = get_logger("forgesight.audit")
    logger.emit(
        LogRecord(
            timestamp=event.timestamp_unix_nanos,
            body=str(event.kind),
            attributes=attributes,
        )
    )
