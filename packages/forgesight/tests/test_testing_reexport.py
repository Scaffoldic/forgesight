"""The `forgesight.testing` facade re-exports the core testing surface."""

from __future__ import annotations

import forgesight
from forgesight.testing import InMemoryExporter, assert_span_tree, find_span
from forgesight.testing.conformance import run_exporter_conformance


def test_testing_reexports_are_callable() -> None:
    assert callable(assert_span_tree)
    assert callable(find_span)
    run_exporter_conformance(InMemoryExporter)  # the re-exported suite runs


def test_end_to_end_via_facade_testing() -> None:
    sink = InMemoryExporter()
    forgesight.configure(exporters=[sink], sync_export=True)
    try:
        with forgesight.telemetry.agent_run("classifier"):
            pass
        assert_span_tree(sink, {"op": "invoke_agent", "name": "classifier"})
    finally:
        forgesight.get_runtime().shutdown()
