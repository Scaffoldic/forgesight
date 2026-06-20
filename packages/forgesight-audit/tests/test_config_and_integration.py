"""End-to-end: the audit listener under a real runtime, plus config/build/install."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import forgesight_audit
from forgesight_audit import (
    AuditKind,
    AuditListener,
    AuditQuery,
    JsonlAuditSink,
    OtelAuditSink,
    build_sink,
    make_audit_listener,
    verify,
)
from forgesight_core import (
    InMemoryExporter,
    configure,
    get_runtime,
    reset_runtime,
    telemetry,
)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_runtime()


def _events(sink: JsonlAuditSink) -> list[AuditKind]:
    return [e.kind for e in sink.query(AuditQuery()).events()]


def test_records_a_run_end_to_end(tmp_path: object) -> None:
    sink = JsonlAuditSink(str(tmp_path) + "/audit.jsonl")  # type: ignore[operator]
    configure(sync_export=True, listeners=[AuditListener(sink)])
    with telemetry.agent_run(
        "payments-approver",
        version="3.1.0",
        metadata={"team": "fin", "owner": "o@x.com", "password": "hunter2"},
    ) as run:
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            call.record_usage(input=10, output=5)
            call.set_cost(0.02)
        with run.tool_call("ledger.post"):
            pass
    get_runtime().force_flush()

    kinds = _events(sink)
    assert AuditKind.RUN_START in kinds
    assert AuditKind.RUN_END in kinds
    assert AuditKind.MODEL_CALL in kinds
    assert AuditKind.TOOL_CALL in kinds
    assert verify(sink).intact

    all_events = list(sink.query(AuditQuery()).events())
    end = next(e for e in all_events if e.kind == AuditKind.RUN_END)
    assert end.principal == "payments-approver"
    assert end.version == "3.1.0"
    assert end.team == "fin"
    assert end.attributes["password"] == "<redacted>"
    assert end.cost_usd == pytest.approx(0.02)  # accumulated run cost
    model = next(e for e in all_events if e.kind == AuditKind.MODEL_CALL)
    assert model.cost_usd == pytest.approx(0.02)
    assert model.principal == "payments-approver"  # resolved via the active run scope


def test_complete_capture_past_sampling(tmp_path: object) -> None:
    sink = JsonlAuditSink(str(tmp_path) + "/audit.jsonl")  # type: ignore[operator]
    exporter = InMemoryExporter()
    configure(
        sample_rate=0.0, sync_export=True, listeners=[AuditListener(sink)], exporters=[exporter]
    )
    with telemetry.agent_run("a") as run, run.llm_call("p", "m") as call:
        call.set_cost(0.01)
    get_runtime().force_flush()
    assert exporter.records == []  # every trace head-sampled out of the exporters
    assert sink.query(AuditQuery()).event_count >= 3  # but the audit log captured it whole
    assert verify(sink).intact


def test_error_emits_error_and_run_end(tmp_path: object) -> None:
    sink = JsonlAuditSink(str(tmp_path) + "/audit.jsonl")  # type: ignore[operator]
    configure(sync_export=True, listeners=[AuditListener(sink)])
    with pytest.raises(ValueError, match="boom"), telemetry.agent_run("a"):
        raise ValueError("boom")
    get_runtime().force_flush()
    kinds = _events(sink)
    assert AuditKind.ERROR in kinds
    assert AuditKind.RUN_END in kinds
    assert verify(sink).intact


def test_install_attaches_to_runtime(tmp_path: object) -> None:
    path = str(tmp_path) + "/installed.jsonl"  # type: ignore[operator]
    configure(sync_export=True)
    listener = forgesight_audit.install({"sink": "jsonl", "path": path})
    assert isinstance(listener, AuditListener)
    with telemetry.agent_run("a"):
        pass
    get_runtime().force_flush()
    assert verify(JsonlAuditSink(path)).intact
    assert JsonlAuditSink(path).query(AuditQuery()).event_count >= 2


def test_configure_listeners_by_name(tmp_path: object) -> None:
    path = str(tmp_path) + "/named.jsonl"  # type: ignore[operator]
    configure(
        sync_export=True, listeners=[{"name": "audit", "config": {"sink": "jsonl", "path": path}}]
    )
    with telemetry.agent_run("a"):
        pass
    get_runtime().force_flush()
    assert JsonlAuditSink(path).query(AuditQuery()).event_count >= 2


def test_build_sink_variants_and_errors(tmp_path: object) -> None:
    assert isinstance(build_sink(sink="jsonl", path=str(tmp_path) + "/a.jsonl"), JsonlAuditSink)  # type: ignore[operator]
    assert isinstance(build_sink(sink="otel"), OtelAuditSink)
    with pytest.raises(ValueError, match="requires"):
        build_sink(sink="jsonl")
    with pytest.raises(ValueError, match="requires"):
        build_sink(sink="sqlite")
    with pytest.raises(ValueError, match="requires"):
        build_sink(sink="siem")
    with pytest.raises(ValueError, match="unknown audit sink"):
        build_sink(sink="does-not-exist")


def test_make_audit_listener_flatten_capture(tmp_path: object) -> None:
    nested = make_audit_listener(
        sink="jsonl",
        path=str(tmp_path) + "/n.jsonl",
        capture={"kinds": ["run.end"]},  # type: ignore[operator]
    )
    assert isinstance(nested, AuditListener)
    flat = make_audit_listener(sink="otel", kinds=["model.call"], redact=False)
    assert isinstance(flat, AuditListener)
