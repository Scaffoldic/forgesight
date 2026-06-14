"""Tests for the Langfuse exporter: auth, observation mapping, trace lift, conformance."""

from __future__ import annotations

import base64

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from forgesight_core import configure, reset_runtime, telemetry
from forgesight_core.testing.conformance import run_exporter_conformance
from forgesight_langfuse import LangfuseExporter, basic_auth_header


def _exporter(sink: InMemorySpanExporter, **kw: object) -> LangfuseExporter:
    return LangfuseExporter(
        public_key="pk-lf-test", secret_key="sk-lf-test", span_exporter=sink, **kw
    )


def test_basic_auth_header() -> None:
    header = basic_auth_header("pk-lf-1", "sk-lf-2")
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == "pk-lf-1:sk-lf-2"


def test_requires_keys() -> None:
    with pytest.raises(ValueError, match="public_key and secret_key"):
        LangfuseExporter(public_key="", secret_key="sk")


def test_region_resolves_host() -> None:
    exporter = LangfuseExporter(public_key="pk", secret_key="sk", region="us")
    assert exporter._host == "https://us.cloud.langfuse.com"
    explicit = LangfuseExporter(public_key="pk", secret_key="sk", host="https://self/")
    assert explicit._host == "https://self"


def test_conformance() -> None:
    run_exporter_conformance(
        lambda: LangfuseExporter(
            public_key="pk-lf", secret_key="sk-lf", span_exporter=InMemorySpanExporter()
        )
    )


def test_observation_types_and_trace_lift() -> None:
    sink = InMemorySpanExporter()
    configure(exporters=[_exporter(sink)], sync_export=True)
    try:
        with telemetry.agent_run("classifier") as run:
            run.set_metadata(user_id="u-1", session_id="s-1")
            with run.step("react-1"), run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=10, output=5)
            with run.tool_call("search"):
                pass
        spans = {s.name: s for s in sink.get_finished_spans()}
        by_attr = {
            s.attributes["langfuse.observation.type"]: s  # type: ignore[index]
            for s in sink.get_finished_spans()
            if s.attributes
        }
        assert "generation" in by_attr  # the LLM call
        assert "tool" in by_attr  # the tool call
        assert "agent" in by_attr  # the run
        agent_span = next(s for s in sink.get_finished_spans() if s.name.startswith("invoke_agent"))
        assert agent_span.attributes is not None
        assert agent_span.attributes["langfuse.trace.name"] == "classifier"
        assert agent_span.attributes["langfuse.user.id"] == "u-1"
        assert agent_span.attributes["langfuse.session.id"] == "s-1"
        assert spans  # sanity
    finally:
        reset_runtime()


def test_cost_is_ingested_as_extension_attr() -> None:
    sink = InMemorySpanExporter()
    configure(exporters=[_exporter(sink)], sync_export=True)
    try:
        with (
            telemetry.agent_run("c") as run,
            run.llm_call("anthropic", "claude-sonnet-4-5") as call,
        ):
            call.record_usage(input=1000, output=500)
        gen = next(
            s
            for s in sink.get_finished_spans()
            if s.attributes and s.attributes.get("langfuse.observation.type") == "generation"
        )
        assert gen.attributes is not None
        assert gen.attributes["forgesight.usage.cost_usd"] is not None  # SDK cost ingested
    finally:
        reset_runtime()
