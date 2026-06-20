"""Listener mapping: taxonomy, attribution, redaction, governance status, isolation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from forgesight_api import EventType, Kind, LifecycleEvent, Record, RunStatus
from forgesight_audit import AuditEvent, AuditKind, AuditListener, AuditQuery, AuditReport


class _CollectSink:
    """A minimal in-memory AuditSink for asserting what the listener emits."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)

    def query(self, q: AuditQuery) -> AuditReport:
        return AuditReport([e for e in self.events if q.matches(e)])

    def export(self, q: AuditQuery, to: str) -> None: ...

    def head_hash(self) -> str | None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None: ...


def _agent_record(
    *,
    status: RunStatus = RunStatus.OK,
    attributes: Mapping[str, object] | None = None,
    name: str = "agent-x",
) -> Record:
    return Record(
        kind=Kind.AGENT,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name=name,
        status=status,
        start_unix_nanos=10,
        end_unix_nanos=20,
        attributes=attributes or {},
    )


def _finish(record: Record, event_type: EventType = EventType.RUN_COMPLETED) -> LifecycleEvent:
    return LifecycleEvent(
        type=event_type,
        run_id=record.run_id,
        unix_nanos=record.end_unix_nanos or 0,
        record=record,
        attributes=record.attributes,
        trace_id=record.trace_id,
        span_id=record.span_id,
    )


def _kinds(events: Sequence[AuditEvent]) -> set[AuditKind]:
    return {e.kind for e in events}


def test_run_events_full_attribution() -> None:
    sink = _CollectSink()
    record = _agent_record(
        name="payments",
        attributes={"agent.version": "3.1.0", "team": "fin", "owner": "o@x.com"},
    )
    AuditListener(sink).on_event(_finish(record))
    assert AuditKind.RUN_START in _kinds(sink.events)
    end = next(e for e in sink.events if e.kind == AuditKind.RUN_END)
    assert end.principal == "payments"
    assert end.version == "3.1.0"
    assert end.team == "fin"
    assert end.owner == "o@x.com"
    assert end.status == str(RunStatus.OK)
    start = next(e for e in sink.events if e.kind == AuditKind.RUN_START)
    assert start.timestamp_unix_nanos == 10
    assert end.timestamp_unix_nanos == 20


def test_governance_status_mapping() -> None:
    for status, expected in (
        (RunStatus.GUARDRAIL, AuditKind.POLICY_DECISION),
        (RunStatus.BUDGET_EXCEEDED, AuditKind.BUDGET_EVENT),
    ):
        sink = _CollectSink()
        AuditListener(sink).on_event(_finish(_agent_record(status=status), EventType.RUN_FAILED))
        assert expected in _kinds(sink.events)


def test_kinds_filter_restricts_output() -> None:
    sink = _CollectSink()
    AuditListener(sink, kinds=[AuditKind.RUN_END]).on_event(_finish(_agent_record()))
    assert _kinds(sink.events) == {AuditKind.RUN_END}


def test_redaction_scrubs_then_keeps_team() -> None:
    sink = _CollectSink()
    AuditListener(sink).on_event(
        _finish(_agent_record(attributes={"password": "hunter2", "team": "t"}))
    )
    end = next(e for e in sink.events if e.kind == AuditKind.RUN_END)
    assert end.attributes["password"] == "<redacted>"
    assert end.team == "t"


def test_redaction_disabled_keeps_raw() -> None:
    sink = _CollectSink()
    AuditListener(sink, redact=False).on_event(
        _finish(_agent_record(attributes={"password": "hunter2"}))
    )
    end = next(e for e in sink.events if e.kind == AuditKind.RUN_END)
    assert end.attributes["password"] == "hunter2"


def test_start_event_without_record_is_ignored() -> None:
    sink = _CollectSink()
    AuditListener(sink).on_event(
        LifecycleEvent(type=EventType.RUN_STARTED, run_id="r", unix_nanos=1)
    )
    assert sink.events == []


def test_on_event_swallows_sink_errors() -> None:
    class _Boom:
        def write(self, event: AuditEvent) -> None:
            raise RuntimeError("sink down")

        def query(self, q: AuditQuery) -> AuditReport:
            return AuditReport([])

        def export(self, q: AuditQuery, to: str) -> None: ...

        def head_hash(self) -> str | None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None: ...

    AuditListener(_Boom()).on_event(_finish(_agent_record()))  # must not raise
