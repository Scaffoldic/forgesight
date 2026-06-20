# Evaluations & human feedback runbook

> Attach run-correlated eval scores and post-hoc human feedback onto the same telemetry pipeline as everything else. **Extra:** `pip install "forgesight[eval]"` · **Spec:** [feat-021](../features/feat-021-agent-evaluations-and-human-feedback.md)

## What it does

`forgesight-eval` lets you record quality signals — automated evals (LLM-as-judge, Ragas, DeepEval) and human thumbs-up/down — against a specific run. `record_evaluation(...)` attaches to the **current** run (it nests as a child span under the run's still-open trace); `record_feedback(...)` attaches to a **past** run by `run_id` (a standalone record carrying the id so your backend re-associates it). Both build an `EvaluationResult`, emit it as a record carrying the OpenTelemetry `gen_ai.evaluation.*` attributes, and publish an `EVALUATION_RECORDED` lifecycle event. Recording is non-blocking and a no-op while the module is disabled.

## When to use it

- You run an LLM-as-judge or a Ragas/DeepEval scorer in-line during a run and want the score on the same trace.
- A reviewer or end-user rates a past run (thumbs-up/down, a 1-5 star, a free-text comment) and you want it joined back by `run_id`.
- You want eval scores streaming to Langfuse scores / Phoenix evaluations / any OTLP sink without a second pipeline.

## Install

```bash
pip install "forgesight[eval]"     # the extra (pulls forgesight-eval)
pip install forgesight-eval        # standalone distribution, if you pin individually
```

## Set up / Configure

The eval module is **off by default** — installing the package emits nothing until switched on (P2). Turn it on either through the `forgesight.modules` entry point (group `forgesight.modules`, name `eval` → `forgesight_eval:install`) by adding a `modules.eval` block to your config, or via env:

```yaml
# forgesight.yaml
modules:
  eval:
    enabled: true
    capture_explanation: true   # gate free-text explanation/comment (also needs global capture_content)
    score_schema:               # optional fail-fast validation at the call site
      faithfulness: { type: numeric, min: 0.0, max: 1.0 }
      verdict:      { type: categorical, labels: [pass, fail] }
```

Env overrides: `FORGESIGHT_EVAL_ENABLED`, `FORGESIGHT_EVAL_EMIT_AS`. You can also call `forgesight_eval.install({"enabled": True})` directly. Then record:

```python
import forgesight
from forgesight_eval import record_evaluation, record_feedback

forgesight.configure(service_name="my-agent")

# Real-time, automated eval — attaches to the CURRENT run as a child span.
record_evaluation(
    "faithfulness",
    score=0.92,
    evaluator="ragas-0.1",
    explanation="grounded in retrieved context",
)

# Post-hoc human feedback — attaches to a PAST run by id.
record_feedback(
    "thumbs",
    run_id="9f1c…",
    label="up",
    comment="answer was correct and concise",
    source="human",
)
```

At least one of `score` / `label` must be set, or the call raises `ValueError`. `record_evaluation` with no `run_id` and no current run raises `RuntimeError`.

## Behavior

- **Attachment.** `record_evaluation` resolves the active run from ambient context (`current_run_scope()` / `current_context()`). A real-time eval reuses the run's `trace_id` and nests under the current span (`realtime=True`); a post-hoc eval or feedback gets a fresh `trace_id` with no parent and carries the `run_id` for the backend to re-associate.
- **Emission.** Both build a frozen `EvaluationResult` and emit a `Kind.STEP` record named `evaluation <name>` with `RunStatus.OK`, then an `EventType.EVALUATION_RECORDED` lifecycle event. Everything flows through the normal export pipeline.
- **Attributes emitted** (otel-semantic-conventions §4.3 + namespaced extensions):
  - `gen_ai.evaluation.name` — the dimension name
  - `gen_ai.evaluation.score.value` — numeric score (when `score` set)
  - `gen_ai.evaluation.score.label` — categorical label (when `label` set)
  - `gen_ai.evaluation.explanation` — free text, only if `capture_explanation` AND the global `capture_content` switch are on
  - `forgesight.run.id`, `forgesight.evaluation.source` (`auto`/`human`), `forgesight.evaluation.realtime`, `forgesight.evaluation.evaluator`
  - any keys you pass in `metadata=` are merged in verbatim.

## Operate it

To verify it is wired and emitting:

1. Set `modules.eval.enabled: true` (or `FORGESIGHT_EVAL_ENABLED=true`) and confirm `forgesight_eval.install(...)` / `get_config().enabled` returns `True`.
2. Inside an active run call `record_evaluation("faithfulness", score=0.92)`, then flush/shutdown so the record exports.
3. In your backend, find the run's trace and look for the child span `evaluation faithfulness` carrying `gen_ai.evaluation.name=faithfulness` and `gen_ai.evaluation.score.value=0.92`. In Langfuse it surfaces as a score; in Phoenix as an evaluation.
4. For feedback, call `record_feedback("thumbs", run_id=…, label="up")` and confirm the standalone record carries `forgesight.run.id` equal to that `run_id`.

To see explanations/comments you must enable BOTH `capture_explanation` (module) and `capture_content` (global) — otherwise the free text is dropped by design (P7).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: record_evaluation has no run_id and no current run` | Called outside a run with no `run_id` | Call inside an active run, or pass `run_id=` |
| `ValueError: must set at least one of score / label` | Both `score` and `label` were `None` | Provide a `score` and/or a `label` |
| `ValueError: score … outside […]` / `label … not in …` | Value violates the `score_schema` | Fix the value or relax/remove the schema entry |
| Nothing appears in the backend | Module still disabled (default) | Set `modules.eval.enabled: true` or `FORGESIGHT_EVAL_ENABLED=true` |
| Score shows but no explanation/comment | `capture_explanation` or global `capture_content` is off | Enable both (free text is gated on the content switch) |
| Feedback not joined to its run | Wrong `run_id`, or backend hasn't re-associated yet | Confirm the exact `run_id`; feedback is standalone and joined downstream by `forgesight.run.id` |
| Records missing after a short script | Process exited before flush | Call `forgesight.force_flush()` / `shutdown()` before exit |

## Reference

- Feature spec: [feat-021](../features/feat-021-agent-evaluations-and-human-feedback.md)
- Package: [`packages/forgesight-eval`](../../packages/forgesight-eval)
- OTel mapping: [otel-semantic-conventions.md](../design/otel-semantic-conventions.md)
- Playbooks: [01-install.md](../playbooks/01-install.md) · [02-instrument-your-agent.md](../playbooks/02-instrument-your-agent.md)
