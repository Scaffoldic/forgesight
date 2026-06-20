"""The audit tap — an ``EventListener`` that projects lifecycle events into the chain.

Because it rides the event bus (feat-007), it sees **every** run's finish events, including
traces head-sampled out of the exporters (the bus is not sampled) — that is "complete
capture" for free. Listeners receive the raw record, so redaction is applied here (P7)
before an ``AuditEvent`` is built.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from forgesight_api import EventType, Kind, LifecycleEvent, Record, RunStatus
from forgesight_core import current_run_scope
from forgesight_core.interceptors import ContentCaptureGate, PIIRedactionInterceptor

from .model import DEFAULT_KINDS, AuditEvent, AuditKind
from .sink import AuditSink

_LOG = logging.getLogger("forgesight.audit")

_RUN_FINISH = frozenset({EventType.RUN_COMPLETED, EventType.RUN_FAILED})
_CALL_TO_KIND = {
    EventType.LLM_EXECUTED: AuditKind.MODEL_CALL,
    EventType.TOOL_EXECUTED: AuditKind.TOOL_CALL,
    EventType.MCP_EXECUTED: AuditKind.TOOL_CALL,
}
_STATUS_TO_KIND = {
    RunStatus.GUARDRAIL: AuditKind.POLICY_DECISION,
    RunStatus.BUDGET_EXCEEDED: AuditKind.BUDGET_EVENT,
}


class AuditListener:
    """Projects lifecycle finish-events into hash-chained ``AuditEvent``s on a sink."""

    def __init__(
        self,
        sink: AuditSink,
        *,
        kinds: Iterable[AuditKind] | None = None,
        redact: bool = True,
        capture_content: bool = False,
    ) -> None:
        self._sink = sink
        self._kinds = frozenset(kinds) if kinds is not None else frozenset(DEFAULT_KINDS)
        self._redact = redact
        self._gate = ContentCaptureGate(capture_content=capture_content)
        self._pii = PIIRedactionInterceptor()
        self._run_cost: dict[str, float] = {}

    # --- EventListener SPI ------------------------------------------------------
    def on_event(self, event: LifecycleEvent) -> None:
        try:
            self._handle(event)
        except Exception:  # never raise (P6) — belt-and-braces over the bus's own isolation
            _LOG.exception("audit listener failed handling %s", event.type)

    # --- mapping ----------------------------------------------------------------
    def _handle(self, event: LifecycleEvent) -> None:
        record = event.record
        if record is None:
            return  # start events carry no record; run.start is synthesized at run end
        redacted = self._redacted(record)
        if redacted is None:
            return  # an interceptor vetoed the record → no span and no audit event
        if event.type in _RUN_FINISH:
            self._on_run_end(redacted)
        elif event.type in _CALL_TO_KIND:
            self._accumulate_cost(redacted)
            llm_cost = redacted.llm.cost_usd if redacted.llm is not None else None
            self._emit(_CALL_TO_KIND[event.type], redacted, cost_usd=llm_cost)
            self._maybe_error(redacted)

    def _redacted(self, record: Record) -> Record | None:
        if not self._redact:
            return record
        gated = self._gate.intercept(record)
        if gated is None:
            return None
        return self._pii.intercept(gated)

    def _on_run_end(self, record: Record) -> None:
        cost = self._run_cost.pop(record.run_id, None)
        self._emit(AuditKind.RUN_START, record, timestamp=record.start_unix_nanos)
        self._emit(
            AuditKind.RUN_END,
            record,
            timestamp=record.end_unix_nanos,
            cost_usd=cost,
            status=str(record.status),
        )
        gov_kind = _STATUS_TO_KIND.get(record.status)
        if gov_kind is not None:
            self._emit(gov_kind, record, status=str(record.status))
        self._maybe_error(record)

    def _accumulate_cost(self, record: Record) -> None:
        if record.llm is not None and record.llm.cost_usd is not None:
            self._run_cost[record.run_id] = (
                self._run_cost.get(record.run_id, 0.0) + record.llm.cost_usd
            )

    def _maybe_error(self, record: Record) -> None:
        if record.error is not None:
            self._emit(
                AuditKind.ERROR,
                record,
                status=record.error.error_type or str(record.status),
            )

    def _emit(
        self,
        kind: AuditKind,
        record: Record,
        *,
        timestamp: int | None = None,
        cost_usd: float | None = None,
        status: str | None = None,
    ) -> None:
        if kind not in self._kinds:
            return
        ts = timestamp if timestamp is not None else record.end_unix_nanos
        if ts is None:
            ts = record.start_unix_nanos
        attributes = {str(k): str(v) for k, v in record.attributes.items()}
        self._sink.write(
            AuditEvent(
                kind=kind,
                timestamp_unix_nanos=ts,
                run_id=record.run_id,
                trace_id=record.trace_id,
                principal=self._principal(record),
                version=attributes.get("agent.version"),
                owner=attributes.get("owner"),
                team=attributes.get("team"),
                cost_usd=cost_usd,
                status=status,
                attributes=attributes,
            )
        )

    def _principal(self, record: Record) -> str:
        """The acting agent. Run/workflow records carry it as ``name``; for a child call we
        resolve the still-active run scope (the run hasn't exited when the child finishes)."""
        if record.kind in (Kind.AGENT, Kind.WORKFLOW):
            return record.name
        scope = current_run_scope()
        if scope is not None:
            return scope.name
        return record.name
