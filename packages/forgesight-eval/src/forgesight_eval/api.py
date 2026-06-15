"""``record_evaluation`` / ``record_feedback`` — run-correlated quality on the same pipeline.

``record_evaluation`` attaches to the **current** run (ambient context) — a real-time eval
nests as a child span under the run's still-open trace. ``record_feedback`` attaches to a
**past** run by ``run_id`` — a standalone record carrying the id so the backend re-associates
it. Both build an :class:`EvaluationResult`, emit it as a record with the OTel
``gen_ai.evaluation.*`` attributes (so it streams to Langfuse scores / Phoenix evaluations /
any OTLP sink, P4) and publish an ``EVALUATION_RECORDED`` event. Non-blocking (P6).
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from forgesight_api import EventType, Kind, LifecycleEvent, Record, RunStatus, new_trace_id
from forgesight_core import current_context, current_run_scope, get_runtime, new_span_id

from ._config import get_config
from .model import EvaluationResult

# OTel GenAI evaluation attributes (otel-semantic-conventions §4.3).
GEN_AI_EVALUATION_NAME = "gen_ai.evaluation.name"
GEN_AI_EVALUATION_SCORE_VALUE = "gen_ai.evaluation.score.value"
GEN_AI_EVALUATION_SCORE_LABEL = "gen_ai.evaluation.score.label"
GEN_AI_EVALUATION_EXPLANATION = "gen_ai.evaluation.explanation"
# namespaced extensions (OTel defines none of these)
FORGESIGHT_EVAL_SOURCE = "forgesight.evaluation.source"
FORGESIGHT_EVAL_REALTIME = "forgesight.evaluation.realtime"
FORGESIGHT_EVAL_EVALUATOR = "forgesight.evaluation.evaluator"
FORGESIGHT_RUN_ID = "forgesight.run.id"


def record_evaluation(
    name: str,
    *,
    score: float | None = None,
    label: str | None = None,
    explanation: str | None = None,
    evaluator: str | None = None,
    run_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Attach an eval (auto) to the current run, or to ``run_id`` if given. No-op if disabled."""
    resolved_run_id = run_id or _current_run_id()
    if resolved_run_id is None:
        raise RuntimeError(
            "record_evaluation has no run_id and no current run; "
            "call it inside a run or pass run_id"
        )
    _validate(name, score, label)
    _emit(
        EvaluationResult(
            name=name,
            run_id=resolved_run_id,
            score=score,
            label=label,
            explanation=explanation,
            evaluator=evaluator,
            source="auto",
            realtime=run_id is None,
            metadata=dict(metadata or {}),
        )
    )


def record_feedback(
    name: str,
    *,
    run_id: str,
    score: float | None = None,
    label: str | None = None,
    comment: str | None = None,
    source: str = "human",
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Attach post-hoc feedback to a past run by ``run_id``. No-op if disabled."""
    _validate(name, score, label)
    _emit(
        EvaluationResult(
            name=name,
            run_id=run_id,
            score=score,
            label=label,
            explanation=comment,
            evaluator=None,
            source=source,
            realtime=False,
            metadata=dict(metadata or {}),
        )
    )


def _current_run_id() -> str | None:
    run = current_run_scope()
    if run is not None and run.run_id:
        return run.run_id
    context = current_context()
    return context.run_id if context is not None else None


def _validate(name: str, score: float | None, label: str | None) -> None:
    if score is None and label is None:
        raise ValueError(f"evaluation {name!r} must set at least one of score / label")
    schema = get_config().score_schema.get(name)
    if not isinstance(schema, Mapping):
        return  # unschema'd dimension — open set, accepted unvalidated
    kind = schema.get("type")
    if kind == "numeric" and score is not None:
        low, high = schema.get("min"), schema.get("max")
        if (low is not None and score < low) or (high is not None and score > high):
            raise ValueError(f"score {score} for {name!r} outside [{low}, {high}]")
    if kind == "categorical" and label is not None:
        labels = schema.get("labels") or ()
        if label not in labels:
            raise ValueError(f"label {label!r} for {name!r} not in {list(labels)}")


def _emit(result: EvaluationResult) -> None:
    config = get_config()
    if not config.enabled:
        return  # module installed but not switched on (P2)
    runtime = get_runtime()
    context = current_context()
    if result.realtime and context is not None:
        trace_id, parent = context.trace_id, context.current_span_id
    else:
        trace_id, parent = new_trace_id(), None  # post-hoc: standalone, re-associated by run_id

    now = time.time_ns()
    record = Record(
        kind=Kind.STEP,
        run_id=result.run_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        parent_span_id=parent,
        name=f"evaluation {result.name}",
        status=RunStatus.OK,
        start_unix_nanos=now,
        end_unix_nanos=now,
        attributes=_attributes(result, config.capture_explanation),
    )
    runtime.emit_record(record)
    runtime.emit_event(
        LifecycleEvent(
            type=EventType.EVALUATION_RECORDED,
            run_id=result.run_id,
            unix_nanos=now,
            record=record,
            trace_id=trace_id,
            span_id=record.span_id,
        )
    )


def _attributes(result: EvaluationResult, capture_explanation: bool) -> dict[str, object]:
    attrs: dict[str, object] = dict(result.metadata)
    attrs[GEN_AI_EVALUATION_NAME] = result.name
    attrs[FORGESIGHT_RUN_ID] = result.run_id
    attrs[FORGESIGHT_EVAL_SOURCE] = result.source
    attrs[FORGESIGHT_EVAL_REALTIME] = result.realtime
    if result.score is not None:
        attrs[GEN_AI_EVALUATION_SCORE_VALUE] = result.score
    if result.label is not None:
        attrs[GEN_AI_EVALUATION_SCORE_LABEL] = result.label
    if result.evaluator is not None:
        attrs[FORGESIGHT_EVAL_EVALUATOR] = result.evaluator
    # explanation/comment is free text → gated by capture_explanation AND the global
    # content-capture switch (P7); dropped if either is off.
    if result.explanation is not None and capture_explanation and _content_capture_on():
        attrs[GEN_AI_EVALUATION_EXPLANATION] = result.explanation
    return attrs


def _content_capture_on() -> bool:
    try:
        return bool(get_runtime().config.capture_content)
    except Exception:  # pragma: no cover - runtime always present in practice
        return False
