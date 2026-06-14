"""The `forgesight` facade re-exports the runtime and works end-to-end."""

from __future__ import annotations

import forgesight
from forgesight_api import Kind


def test_facade_reexports() -> None:
    assert callable(forgesight.configure)
    assert callable(forgesight.instrument)
    assert forgesight.telemetry is not None
    assert forgesight.__version__


def test_end_to_end_via_facade() -> None:
    mem = forgesight.InMemoryExporter()
    forgesight.configure(exporters=[mem], service_name="my-agent")
    assert forgesight.current_run() is None
    with forgesight.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
        assert forgesight.current_run() is run
        with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
            call.record_usage(input=1200, output=350, cache_read=800)
    forgesight.get_runtime().force_flush()  # drain the async pipeline
    kinds = sorted({r.kind for r in mem.records})
    assert kinds == [Kind.AGENT, Kind.LLM]
    forgesight.get_runtime().shutdown()


def test_configure_default_console_exporter() -> None:
    rt = forgesight.configure()
    assert len(rt.exporters) == 1
    assert isinstance(rt.exporters[0], forgesight.ConsoleExporter)
