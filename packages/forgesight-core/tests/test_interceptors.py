"""Tests for the built-in interceptors: content gate + PII redaction."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from forgesight_api import Content, Kind, LLMCall, Record, RunStatus
from forgesight_core import (
    ContentCaptureGate,
    InMemoryExporter,
    PIIRedactionInterceptor,
    configure,
    get_runtime,
    reset_runtime,
    telemetry,
)


def _llm_record(
    *, content: Content | None = None, params: dict | None = None, attributes: dict | None = None
) -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="m",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        attributes=MappingProxyType(attributes or {}),
        llm=LLMCall(provider="anthropic", request_model="m", content=content, params=params or {}),
    )


# --- ContentCaptureGate ----------------------------------------------------
def test_gate_strips_content_when_capture_off() -> None:
    rec = _llm_record(content=Content(input_messages=[{"role": "user"}]))
    out = ContentCaptureGate(capture_content=False).intercept(rec)
    assert out is not None
    assert out.llm is not None
    assert out.llm.content is None


def test_gate_preserves_content_when_capture_on() -> None:
    rec = _llm_record(content=Content(input_messages=[{"role": "user"}]))
    out = ContentCaptureGate(capture_content=True).intercept(rec)
    assert out is not None
    assert out.llm is not None
    assert out.llm.content is not None


def test_gate_passes_through_records_without_content() -> None:
    rec = _llm_record(content=None)
    assert ContentCaptureGate().intercept(rec) is rec


# --- PIIRedactionInterceptor ----------------------------------------------
def test_redaction_by_key_substring_case_insensitive() -> None:
    rec = _llm_record(attributes={"customer_SSN": "123-45-6789", "team": "platform"})
    out = PIIRedactionInterceptor(redact_keys=("ssn",)).intercept(rec)
    assert out is not None
    assert out.attributes["customer_SSN"] == "<redacted>"
    assert out.attributes["team"] == "platform"


def test_redaction_recurses_into_nested_dicts() -> None:
    rec = _llm_record(attributes={"headers": {"Authorization": "Bearer sk-secret"}})
    out = PIIRedactionInterceptor(redact_keys=("authorization",)).intercept(rec)
    assert out is not None
    assert out.attributes["headers"]["Authorization"] == "<redacted>"  # type: ignore[index]


def test_redaction_by_pattern() -> None:
    rec = _llm_record(attributes={"note": "call me at 123-45-6789 today"})
    out = PIIRedactionInterceptor(redact_patterns=(r"\b\d{3}-\d{2}-\d{4}\b",)).intercept(rec)
    assert out is not None
    assert "123-45-6789" not in str(out.attributes["note"])
    assert "<redacted>" in str(out.attributes["note"])


def test_redaction_key_wins_over_pattern() -> None:
    rec = _llm_record(attributes={"secret": "123-45-6789"})
    out = PIIRedactionInterceptor(redact_keys=("secret",), redact_patterns=(r"\d",)).intercept(rec)
    assert out is not None
    assert out.attributes["secret"] == "<redacted>"  # whole value, not per-digit


def test_redaction_applies_to_llm_params() -> None:
    rec = _llm_record(params={"api_key": "sk-123", "temperature": 0.2})
    out = PIIRedactionInterceptor().intercept(rec)
    assert out is not None
    assert out.llm is not None
    assert out.llm.params["api_key"] == "<redacted>"
    assert out.llm.params["temperature"] == 0.2


def test_bad_pattern_fails_fast() -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 - re.error subclass
        PIIRedactionInterceptor(redact_patterns=("(",))


# --- integration through the runtime --------------------------------------
def test_gate_is_prepended_by_configure() -> None:
    rt = configure(sync_export=True)
    try:
        assert isinstance(rt.interceptors[0], ContentCaptureGate)
    finally:
        reset_runtime()


def test_runtime_redacts_metadata_across_export() -> None:
    mem = InMemoryExporter()
    configure(
        sync_export=True,
        exporters=[mem],
        interceptors=[PIIRedactionInterceptor(redact_keys=("secret",))],
    )
    try:
        with telemetry.agent_run("c") as run:
            run.set_metadata(secret="xyz", team="platform")
        agent = next(r for r in mem.records if r.kind is Kind.AGENT)
        assert agent.attributes["secret"] == "<redacted>"
        assert agent.attributes["team"] == "platform"
    finally:
        reset_runtime()


def test_interceptor_veto_drops_record() -> None:
    mem = InMemoryExporter()

    class DropAgents:
        def intercept(self, record: Record) -> Record | None:
            return None if record.kind is Kind.AGENT else record

    configure(sync_export=True, exporters=[mem], interceptors=[DropAgents()])
    try:
        with telemetry.agent_run("c") as run, run.tool_call("search"):
            pass
        kinds = {r.kind for r in mem.records}
        assert Kind.AGENT not in kinds  # vetoed
        assert Kind.TOOL in kinds
        assert get_runtime().dropped >= 1
    finally:
        reset_runtime()
