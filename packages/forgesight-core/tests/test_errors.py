"""Tests for error & exception capture (FR-7): record-then-re-raise, ErrorInfo."""

from __future__ import annotations

import pytest

from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, configure, reset_runtime, telemetry


class _RateLimitError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def _agent(mem: InMemoryExporter):  # type: ignore[no-untyped-def]
    return next(r for r in mem.records if r.kind is Kind.AGENT)


def test_exception_is_recorded_and_reraised() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem])
    try:
        with (
            pytest.raises(_RateLimitError),
            telemetry.agent_run("payments") as run,
            run.llm_call("anthropic", "m"),
        ):
            raise _RateLimitError("429 from provider", code="rate_limited")
        llm = next(r for r in mem.records if r.kind is Kind.LLM)
        assert llm.status is RunStatus.ERROR
        assert llm.error is not None
        assert llm.error.error_type == "_RateLimitError"
        assert "429" in llm.error.message
        assert llm.error.code == "rate_limited"
        assert llm.error.stacktrace is not None
    finally:
        reset_runtime()


def test_callers_handler_still_runs() -> None:
    configure(sync_export=True)
    handled = False
    try:
        try:
            with telemetry.agent_run("c"):
                raise ValueError("boom")
        except ValueError:
            handled = True  # the SDK did NOT swallow it
        assert handled
    finally:
        reset_runtime()


def test_run_rollup_sets_error_status() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem])
    try:
        with pytest.raises(ValueError, match="x"), telemetry.agent_run("c"):
            raise ValueError("x")
        agent = _agent(mem)
        assert agent.status is RunStatus.ERROR
        assert agent.error is not None
        assert agent.error.error_type == "ValueError"
    finally:
        reset_runtime()


def test_record_error_does_not_reraise() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem])
    try:
        with telemetry.agent_run("batch") as run:
            try:
                raise RuntimeError("enrich failed")
            except RuntimeError as exc:
                run.record_error(exc, code="enrich_failed")  # records, no re-raise
        agent = _agent(mem)
        assert agent.status is RunStatus.ERROR
        assert agent.error is not None
        assert agent.error.code == "enrich_failed"
    finally:
        reset_runtime()


def test_stack_capture_depth_zero_means_no_stacktrace() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem], stack_capture_depth=0)
    try:
        with pytest.raises(ValueError, match="x"), telemetry.agent_run("c"):
            raise ValueError("x")
        agent = _agent(mem)
        assert agent.error is not None
        assert agent.error.stacktrace is None
        assert agent.error.error_type == "ValueError"  # type still captured
    finally:
        reset_runtime()


def test_capture_stacktrace_false() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem], capture_stacktrace=False)
    try:
        with pytest.raises(ValueError, match="x"), telemetry.agent_run("c"):
            raise ValueError("x")
        agent = _agent(mem)
        assert agent.error is not None
        assert agent.error.stacktrace is None
    finally:
        reset_runtime()


def test_successful_run_carries_no_error_info() -> None:
    mem = InMemoryExporter()
    configure(sync_export=True, exporters=[mem])
    try:
        with telemetry.agent_run("c"):
            pass
        agent = _agent(mem)
        assert agent.status is RunStatus.OK
        assert agent.error is None
    finally:
        reset_runtime()
