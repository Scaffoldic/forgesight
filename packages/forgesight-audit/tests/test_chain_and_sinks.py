"""Chain integrity, the drivers, query/export, and the conformance suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgesight_audit import (
    AuditEvent,
    AuditKind,
    AuditQuery,
    JsonlAuditSink,
    OtelAuditSink,
    SiemAuditSink,
    SqliteAuditSink,
    canonical_bytes,
    verify,
)
from forgesight_audit.sinks.base import _ChainedSink
from forgesight_audit.testing import run_audit_sink_conformance


def _event(seq_hint: int, **kw: object) -> AuditEvent:
    base: dict[str, object] = {
        "kind": AuditKind.MODEL_CALL,
        "timestamp_unix_nanos": 1000 + seq_hint,
        "run_id": "01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "principal": "agent-x",
        "cost_usd": float(seq_hint),
    }
    base.update(kw)
    return AuditEvent(**base)  # type: ignore[arg-type]


# --- model + chain ------------------------------------------------------------
def test_audit_event_roundtrip() -> None:
    ev = _event(1, owner="o@x.com", team="t", attributes={"a": "b"})
    assert AuditEvent.from_dict(ev.to_dict()) == ev


def test_canonical_is_stable_and_excludes_hash_fields() -> None:
    ev = _event(2, hash="DEADBEEF", prev_hash="CAFE")
    other = AuditEvent.from_dict({**ev.to_dict(), "hash": "different", "prev_hash": "x"})
    assert canonical_bytes(ev) == canonical_bytes(other)


def test_query_matches() -> None:
    ev = _event(1, principal="p", team="t", timestamp_unix_nanos=100)
    assert AuditQuery().matches(ev)
    assert AuditQuery(principal="p").matches(ev)
    assert not AuditQuery(principal="other").matches(ev)
    assert not AuditQuery(team="z").matches(ev)
    assert AuditQuery(since=100, until=101).matches(ev)
    assert not AuditQuery(since=101).matches(ev)
    assert not AuditQuery(kind=AuditKind.TOOL_CALL).matches(ev)


def test_bad_algorithm_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported hash algorithm"):
        JsonlAuditSink(str(tmp_path / "a.jsonl"), algorithm="md5")


# --- jsonl driver -------------------------------------------------------------
def test_jsonl_write_query_verify_export(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.jsonl")
    sink = JsonlAuditSink(path)
    for i in range(5):
        sink.write(_event(i, principal="a" if i % 2 else "b"))
    assert sink.head_hash() is not None
    assert verify(sink).intact
    assert sink.query(AuditQuery()).event_count == 5
    assert sink.query(AuditQuery(principal="a")).event_count == 2
    assert sink.query(AuditQuery()).cost_usd_total == pytest.approx(0 + 1 + 2 + 3 + 4)

    bundle = str(tmp_path / "full.bundle")
    sink.export(AuditQuery(), to=bundle)
    manifest = json.loads(Path(bundle + ".manifest.json").read_text())
    assert manifest["head_hash"] == sink.head_hash()
    assert manifest["event_count"] == 5
    assert verify(JsonlAuditSink(bundle)).intact


def test_jsonl_resumes_chain_on_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "resume.jsonl")
    first = JsonlAuditSink(path)
    first.write(_event(0))
    first.write(_event(1))
    head = first.head_hash()
    second = JsonlAuditSink(path)
    second.write(_event(2))
    assert second.query(AuditQuery()).event_count == 3
    assert verify(second).intact
    events = list(second.query(AuditQuery()).events())
    assert events[2].prev_hash == head
    assert events[2].seq == 2


def test_jsonl_tamper_detection(tmp_path: Path) -> None:
    log = tmp_path / "tamper.jsonl"
    sink = JsonlAuditSink(str(log))
    for i in range(4):
        sink.write(_event(i))
    lines = log.read_text().splitlines()

    altered = json.loads(lines[1])
    altered["principal"] = "evil"
    log.write_text("\n".join([lines[0], json.dumps(altered), lines[2], lines[3]]) + "\n")
    res = verify(JsonlAuditSink(str(log)))
    assert not res.intact
    assert res.broken_at == 1
    assert res.reason == "altered"

    log.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")
    res = verify(JsonlAuditSink(str(log)))
    assert not res.intact
    assert res.reason in {"deleted", "reordered"}

    log.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n")
    assert not verify(JsonlAuditSink(str(log))).intact


# --- sqlite driver ------------------------------------------------------------
def test_sqlite_write_query_verify() -> None:
    sink = SqliteAuditSink(":memory:")
    for i in range(4):
        sink.write(_event(i, team="t" if i < 2 else "u"))
    assert verify(sink).intact
    assert sink.query(AuditQuery(team="t")).event_count == 2
    assert sink.force_flush() is True
    sink.shutdown()


def test_sqlite_resumes_from_file(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.db")
    one = SqliteAuditSink(path)
    one.write(_event(0))
    one.shutdown()
    two = SqliteAuditSink(path)
    two.write(_event(1))
    assert two.query(AuditQuery()).event_count == 2
    assert verify(two).intact
    two.shutdown()


# --- otel + siem bridges ------------------------------------------------------
def test_otel_sink_emits_and_isolates_failure() -> None:
    seen: list[AuditEvent] = []
    sink = OtelAuditSink(emit=seen.append)
    sink.write(_event(0))
    sink.write(_event(1))
    assert len(seen) == 2
    assert verify(sink).intact

    def _boom(_e: AuditEvent) -> None:
        raise RuntimeError("emit down")

    failing = OtelAuditSink(emit=_boom)
    failing.write(_event(0))  # must not raise
    assert failing.emit_failures == 1
    assert verify(failing).intact  # the chain is intact despite the bridge failing


def test_siem_file_and_injected_transport(tmp_path: Path) -> None:
    out = tmp_path / "siem.log"
    file_sink = SiemAuditSink(endpoint=str(out))
    file_sink.write(_event(0))
    file_sink.write(_event(1))
    assert len(out.read_text().splitlines()) == 2

    lines: list[str] = []
    injected = SiemAuditSink(transport=lines.append)
    injected.write(_event(0))
    assert len(lines) == 1
    assert verify(injected).intact

    unconfigured = SiemAuditSink()  # no endpoint, no transport
    unconfigured.write(_event(0))  # must not raise
    assert unconfigured.emit_failures == 1


# --- conformance for every driver --------------------------------------------
def test_conformance_all_drivers(tmp_path: Path) -> None:
    run_audit_sink_conformance(lambda: JsonlAuditSink(str(tmp_path / "c.jsonl")))
    run_audit_sink_conformance(lambda: SqliteAuditSink(":memory:"))
    run_audit_sink_conformance(lambda: OtelAuditSink(emit=lambda _e: None))
    run_audit_sink_conformance(lambda: SiemAuditSink(transport=lambda _line: None))


def test_chained_sink_abstract_hooks() -> None:
    base = _ChainedSink()
    with pytest.raises(NotImplementedError):
        base._append(_event(0))
    with pytest.raises(NotImplementedError):
        list(base._read_all())
