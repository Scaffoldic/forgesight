# forgesight-eval

Run-correlated **eval scores and human feedback** for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Quality joins cost and structure on the *same* `run_id`, on the *same* pipeline, in the *same*
backends — Langfuse scores, Phoenix evaluations, any OTLP sink — for two function calls.

```bash
pip install forgesight-eval
```

```python
from forgesight import telemetry
from forgesight_eval import record_evaluation, record_feedback

with telemetry.agent_run("rag-answerer", metadata={"prompt_version": "v7"}) as run:
    answer = await answer_question(...)
    # an automated eval (LLM-as-judge / Ragas / DeepEval) → one call, correlated to the live run
    record_evaluation("faithfulness", score=0.91, label="pass",
                      explanation="All claims grounded.", evaluator="ragas")

# hours later, from a webhook — post-hoc human feedback, by run_id:
def on_thumbs_down(run_id: str, comment: str) -> None:
    record_feedback("user_satisfaction", run_id=run_id, label="thumbs_down",
                    score=0.0, comment=comment, source="human")
```

## How it works

- **`record_evaluation`** attaches to the **current** run (ambient context) — a real-time eval
  nests as a child span under the run's open trace. **`record_feedback`** attaches to a **past**
  run by `run_id` — a standalone record carrying the id so the backend re-associates it.
- Both emit the OTel **`gen_ai.evaluation.*`** attributes (`name` / `score.value` / `score.label`
  / `explanation`) plus `forgesight.evaluation.*` extensions (`source`, `realtime`, `evaluator`),
  and publish an `EVALUATION_RECORDED` lifecycle event. Backends that speak the convention
  (Langfuse, Phoenix) display them as scores/evaluations with **no per-backend code** (P4).
- **Non-blocking** (P6): both build a record and enqueue — no network I/O on the agent or the
  webhook. **Secure by default** (P7): `explanation` / `comment` text is gated by
  `capture_explanation` *and* the global content-capture switch.

## Configuration

```yaml
modules:
  eval:
    enabled: true              # master switch (default false — install ≠ active)
    emit_as: "span"            # span | event
    capture_explanation: true  # still gated by the global content-capture switch (P7)
    score_schema:              # optional — validates score/label at the call site
      faithfulness:      { type: "numeric", min: 0.0, max: 1.0 }
      user_satisfaction: { type: "categorical", labels: ["thumbs_up", "thumbs_down"] }
```

At least one of `score` / `label` must be set. Schema'd dimensions are validated (numeric range,
categorical membership); un-schema'd dimensions are accepted unvalidated (open set).

## Out of scope

Running the evaluations (judges/metrics live in the agent or a library — this is the transport),
a dashboard, and score storage/aggregation (the backend's job).

## License

Apache-2.0
