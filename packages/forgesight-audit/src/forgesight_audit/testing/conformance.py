"""The ``AuditSink`` prose contract as an executable suite (P10).

Every shipped and third-party sink is expected to pass: ``write`` never raises and chains,
the chain ``verify()``s intact, ``query`` is consistent, ``head_hash`` advances, and
``force_flush``/``shutdown`` are idempotent (with post-shutdown ``write`` a safe no-op).
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import AuditEvent, AuditKind, AuditQuery
from ..sink import AuditSink, verify


def _sample_event(seq_hint: int) -> AuditEvent:
    return AuditEvent(
        kind=AuditKind.MODEL_CALL,
        timestamp_unix_nanos=1_000 + seq_hint,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        principal="conformance-agent",
        cost_usd=float(seq_hint),
    )


def run_audit_sink_conformance(factory: Callable[[], AuditSink]) -> None:
    """Drive a sink through the invariants its Protocol promises. Raises ``AssertionError``."""
    sink = factory()

    # write never raises; the chain advances.
    for index in range(3):
        sink.write(_sample_event(index))
    assert sink.head_hash() is not None, "head_hash must advance after writes"

    # query is consistent and the chain verifies intact.
    report = sink.query(AuditQuery())
    assert report.event_count >= 3, "query() must return the written events"
    assert isinstance(report.cost_usd_total, float)
    result = verify(sink)
    assert result.intact, f"a freshly written chain must verify intact: {result}"
    assert result.event_count == report.event_count

    # a targeted query filters.
    none_match = sink.query(AuditQuery(principal="nobody"))
    assert none_match.event_count == 0

    # force_flush returns a bool and is idempotent.
    assert isinstance(sink.force_flush(), bool)
    assert isinstance(sink.force_flush(), bool)

    # shutdown is idempotent; write after shutdown must not raise.
    sink.shutdown()
    sink.shutdown()
    sink.write(_sample_event(99))  # no-op, must not raise
