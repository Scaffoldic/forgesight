"""Tests for eval/feedback: ambient vs by-id, gen_ai.evaluation.* mapping, schema, gating."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgesight_api import EventType, LifecycleEvent
from forgesight_core import InMemoryExporter, configure, reset_runtime, telemetry
from forgesight_eval import EvaluationResult, install, record_evaluation, record_feedback
from forgesight_eval._config import reset_config


class _Listener:
    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []

    def on_event(self, event: LifecycleEvent) -> None:
        self.events.append(event)


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    listener = _Listener()
    install({"enabled": True})
    configure(exporters=[exporter], listeners=[listener], sync_export=True, capture_content=True)
    exporter.listener = listener  # type: ignore[attr-defined]
    try:
        yield exporter
    finally:
        reset_runtime()
        reset_config()


def _eval_records(sink: InMemoryExporter) -> list:
    return [r for r in sink.records if r.name.startswith("evaluation ")]


# --- real-time eval -----------------------------------------------------------
def test_record_evaluation_attaches_to_current_run(sink: InMemoryExporter) -> None:
    with telemetry.agent_run("rag") as run:
        record_evaluation(
            "faithfulness", score=0.91, label="pass", explanation="grounded", evaluator="ragas"
        )
        run_id = run.run_id
        trace_id = run.trace_id
    [rec] = _eval_records(sink)
    assert rec.run_id == run_id
    assert rec.trace_id == trace_id  # nested in the run's trace
    assert rec.parent_span_id is not None
    assert rec.attributes["gen_ai.evaluation.name"] == "faithfulness"
    assert rec.attributes["gen_ai.evaluation.score.value"] == 0.91
    assert rec.attributes["gen_ai.evaluation.score.label"] == "pass"
    assert rec.attributes["gen_ai.evaluation.explanation"] == "grounded"
    assert rec.attributes["forgesight.evaluation.source"] == "auto"
    assert rec.attributes["forgesight.evaluation.realtime"] is True
    assert rec.attributes["forgesight.evaluation.evaluator"] == "ragas"


def test_evaluation_emits_event(sink: InMemoryExporter) -> None:
    with telemetry.agent_run("rag"):
        record_evaluation("relevance", score=0.7)
    events = [e for e in sink.listener.events if e.type is EventType.EVALUATION_RECORDED]  # type: ignore[attr-defined]
    assert len(events) == 1
    assert events[0].record is not None


def test_evaluation_carries_metadata(sink: InMemoryExporter) -> None:
    with telemetry.agent_run("rag"):
        record_evaluation("faithfulness", score=0.5, metadata={"judge_model": "claude-sonnet-4-5"})
    [rec] = _eval_records(sink)
    assert rec.attributes["judge_model"] == "claude-sonnet-4-5"


def test_evaluation_explicit_run_id_is_not_realtime(sink: InMemoryExporter) -> None:
    record_evaluation("faithfulness", score=0.8, run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A")
    [rec] = _eval_records(sink)
    assert rec.run_id == "01J9Z3K7P8QF2R5V6W7X8Y9Z0A"
    assert rec.attributes["forgesight.evaluation.realtime"] is False
    assert rec.parent_span_id is None  # standalone, no live trace


# --- post-hoc feedback --------------------------------------------------------
def test_record_feedback_by_run_id(sink: InMemoryExporter) -> None:
    record_feedback(
        "user_satisfaction",
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        label="thumbs_down",
        score=0.0,
        comment="hallucinated the date",
    )
    [rec] = _eval_records(sink)
    assert rec.run_id == "01J9Z3K7P8QF2R5V6W7X8Y9Z0A"
    assert rec.attributes["gen_ai.evaluation.score.label"] == "thumbs_down"
    assert rec.attributes["forgesight.evaluation.source"] == "human"
    assert rec.attributes["forgesight.evaluation.realtime"] is False
    assert rec.attributes["gen_ai.evaluation.explanation"] == "hallucinated the date"


# --- validation ---------------------------------------------------------------
def test_evaluation_requires_score_or_label(sink: InMemoryExporter) -> None:
    with telemetry.agent_run("r"), pytest.raises(ValueError, match="at least one of score"):
        record_evaluation("faithfulness")


def test_evaluation_outside_run_without_id_raises(sink: InMemoryExporter) -> None:
    with pytest.raises(RuntimeError, match="no current run"):
        record_evaluation("faithfulness", score=0.5)


def test_score_schema_numeric_range() -> None:
    install(
        {
            "enabled": True,
            "score_schema": {"faithfulness": {"type": "numeric", "min": 0.0, "max": 1.0}},
        }
    )
    configure(exporters=[InMemoryExporter()], sync_export=True)
    try:
        with pytest.raises(ValueError, match="outside"):
            record_evaluation("faithfulness", score=1.5, run_id="r")
        record_evaluation("faithfulness", score=0.5, run_id="r")  # in range ⇒ ok
    finally:
        reset_runtime()
        reset_config()


def test_score_schema_categorical() -> None:
    install(
        {
            "enabled": True,
            "score_schema": {
                "user_satisfaction": {"type": "categorical", "labels": ["thumbs_up", "thumbs_down"]}
            },
        }
    )
    configure(exporters=[InMemoryExporter()], sync_export=True)
    try:
        with pytest.raises(ValueError, match="not in"):
            record_feedback("user_satisfaction", run_id="r", label="meh")
        record_feedback("user_satisfaction", run_id="r", label="thumbs_up")  # valid label
    finally:
        reset_runtime()
        reset_config()


def test_unschemad_dimension_accepted() -> None:
    install(
        {"enabled": True, "score_schema": {"faithfulness": {"type": "numeric", "min": 0, "max": 1}}}
    )
    configure(exporters=[InMemoryExporter()], sync_export=True)
    try:
        record_evaluation("novel_metric", score=42.0, run_id="r")  # not in schema ⇒ unvalidated
    finally:
        reset_runtime()
        reset_config()


# --- enable switch + privacy --------------------------------------------------
def test_disabled_module_is_noop() -> None:
    install({"enabled": False})
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        record_evaluation("faithfulness", score=0.9, run_id="r")
        assert exporter.records == []  # installed but not switched on ⇒ nothing emitted
    finally:
        reset_runtime()
        reset_config()


def test_explanation_dropped_when_content_capture_off() -> None:
    install({"enabled": True})
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, capture_content=False)  # P7: content off
    try:
        record_evaluation("faithfulness", score=0.9, explanation="contains PII", run_id="r")
        [rec] = [r for r in exporter.records if r.name.startswith("evaluation ")]
        assert "gen_ai.evaluation.explanation" not in rec.attributes  # text dropped
    finally:
        reset_runtime()
        reset_config()


def test_explanation_dropped_when_capture_explanation_off() -> None:
    install({"enabled": True, "capture_explanation": False})
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, capture_content=True)
    try:
        record_evaluation("faithfulness", score=0.9, explanation="hidden", run_id="r")
        [rec] = [r for r in exporter.records if r.name.startswith("evaluation ")]
        assert "gen_ai.evaluation.explanation" not in rec.attributes
    finally:
        reset_runtime()
        reset_config()


# --- model + config -----------------------------------------------------------
def test_evaluation_result_defaults() -> None:
    result = EvaluationResult(name="x", run_id="r", score=1.0)
    assert result.source == "auto"
    assert result.realtime is True
    assert result.metadata == {}


def test_install_returns_enabled() -> None:
    try:
        assert install({"enabled": True}) is True
        assert install({"enabled": False}) is False
    finally:
        reset_config()


def test_install_rejects_bad_emit_as() -> None:
    try:
        with pytest.raises(ValueError, match="emit_as"):
            install({"emit_as": "telegram"})
    finally:
        reset_config()


def test_config_lazy_loads_from_settings() -> None:
    reset_config()
    from forgesight_eval._config import get_config

    assert get_config().enabled is False  # no config ⇒ default disabled
    reset_config()


# --- fan-out to two exporters -------------------------------------------------
def test_evaluation_fans_out_to_two_exporters() -> None:
    install({"enabled": True})
    a, b = InMemoryExporter(), InMemoryExporter()
    configure(exporters=[a, b], sync_export=True)
    try:
        with telemetry.agent_run("r"):
            record_evaluation("faithfulness", score=0.9)
        assert any(r.name.startswith("evaluation ") for r in a.records)
        assert any(r.name.startswith("evaluation ") for r in b.records)
    finally:
        reset_runtime()
        reset_config()
