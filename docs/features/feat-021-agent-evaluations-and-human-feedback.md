# feat-021: Agent evaluations & human feedback

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-021 |
| **Title** | Agent evaluations & human feedback — eval result spans/events + feedback/score capture |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.3 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-eval` |
| **Depends on** | feat-007 (event bus), feat-002 (telemetry runtime) |
| **Blocks** | none |

---

## 1. Why this feature

The SDK records what an agent *did* and what it *cost*. The next question every team
asks is: **was it any good?** Today that signal — eval scores and human feedback —
lives in a different system from the run telemetry, keyed differently, and stitched
together by hand.

Concrete scenarios that hit teams today:

- A nightly LLM-as-judge scores 5,000 summarisation runs for faithfulness. The scores
  land in a CSV; the runs' traces land in Phoenix. Correlating "this 0.41-faithfulness
  score belongs to *that* trace, which made *those* tool calls and cost \$0.03" is a
  manual join on timestamps that nobody trusts.
- A support agent ships a thumbs-up/thumbs-down button. The feedback goes to a product
  analytics table; the run it refers to is in Langfuse. There is no shared key, so
  "show me every run a user marked bad and what the agent actually did" is unanswerable.
- A team wants to compare two prompt versions on a quality metric. They have cost and
  latency per run (the SDK gives them that), but the quality number is in a spreadsheet,
  so the comparison is half-instrumented.
- An eval framework (Ragas, DeepEval, a hand-rolled judge) produces a score with an
  explanation. There is no standard way to attach that score *to the run it evaluated*
  so it flows to whatever backend the team already uses.

Evaluation and feedback are the third leg of agent telemetry, after structure/cost and
governance. The data exists; what's missing is a **standard, run-correlated way to
record it** so it rides the same pipeline, carries the same `run_id`, and lands in the
backends that already know how to display scores.

## 2. Why this belongs in the SDK

- **`run_id` is the correlation key, and the SDK owns it.** An eval score or a human
  thumbs-down is worthless unless it points at the exact run it judges. The SDK already
  mints the `run_id` (ULID, FR-1) and threads it through every span and event. A
  standalone eval library cannot attach to a run it doesn't own the id of; the SDK can,
  and that is the whole value.
- **OTel already defines the shape.** The GenAI conventions specify
  `gen_ai.evaluation.*` attributes (`gen_ai.evaluation.name`,
  `gen_ai.evaluation.score.value`, `gen_ai.evaluation.score.label`,
  `gen_ai.evaluation.explanation`). The SDK is the one place that maps the domain model
  onto those identifiers deterministically (otel-semantic-conventions §4). Emitting
  eval results *through* the SDK means they get the canonical mapping for free; emitting
  them in each agent means re-deriving the attribute names and getting them subtly wrong.
- **It rides the existing event bus + pipeline.** feat-007 already publishes lifecycle
  events to listeners; feat-002/003 already batch and fan-out records non-blockingly
  (P6). An eval result is just another record on that pipeline — so it inherits fault
  isolation, multi-backend fan-out (FR-11), and the non-blocking guarantee. A bespoke
  eval emitter re-invents all of it.
- **Backends already consume `gen_ai.evaluation.*`.** Langfuse has *scores*; Phoenix
  has *evaluations*. Because the SDK speaks the OTel convention as its wire format
  (P4), an eval recorded once streams to *both* — and to any OTLP backend — with no
  per-backend code. The agent author records a score once; the platform team picks
  where it lands.
- **The anti-pattern if we don't:** every team builds a different eval-to-telemetry
  bridge, scores are keyed differently from runs, the join is manual and untrusted, and
  comparing two agents' quality is impossible — exactly the "no two agents are
  comparable" disease requirements §1.1 names, now applied to quality instead of cost.

This is FR-8 (event publishing) extended to the evaluation domain, and the second half
of roadmap **Phase 3 (governance)** — "agent evaluations + human feedback capture."

## 3. How consuming agents/teams benefit

**Before.** An agent author who wants quality signal writes their own bridge: capture
the `run_id` somehow, POST the score to Langfuse's score API in one place and Phoenix's
eval API in another, invent a schema for "score + label + explanation," and reconcile
real-time judge scores against after-the-fact human feedback by hand. ~100 lines per
agent, wrong attribute names, and a different schema in every codebase.

**After.**

- **Day 0 — one call attaches a real-time eval to the live run.**
  `record_evaluation("faithfulness", score=0.91, explanation="…")` inside the run
  emits a `gen_ai.evaluation.*` span/event correlated to the current `run_id`. It
  streams to every configured backend (Langfuse score, Phoenix evaluation, any OTLP
  sink) with zero per-backend code.
- **Day 3 — human feedback after the run, keyed by `run_id`.**
  `record_feedback(run_id, label="thumbs_down", comment="hallucinated the date")` from
  a webhook handler attaches a post-hoc score to a run that finished hours ago. Same
  schema, same backends — the thumbs-down lands next to the trace it judges.
- **Day 7 — A/B quality comparison for free.** Because every score carries the run's
  business metadata (FR-5: `prompt_version`, `team`), the backend can group quality by
  prompt version next to the cost and latency the SDK already emits — the comparison is
  fully instrumented, no spreadsheet.
- **Swap the eval framework without touching emission.** Ragas, DeepEval, or a custom
  judge all funnel through the same `record_evaluation` call. Change the judge; the
  telemetry shape and the backend wiring are untouched.
- **The win:** quality joins cost and structure on the *same* `run_id`, on the *same*
  pipeline, in the *same* backends — an agent author gets run-correlated, multi-backend
  eval + feedback for two function calls instead of a custom bridge.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — real-time eval inside a run (correlated to the live run_id)
from forgesight import telemetry
from forgesight_eval import record_evaluation, record_feedback

with telemetry.agent_run("rag-answerer", version="3.0.0",
                         metadata={"prompt_version": "v7"}) as run:
    answer = await answer_question(...)

    # An automated eval (LLM-as-judge, Ragas, DeepEval, whatever) → one call.
    record_evaluation(
        name="faithfulness",
        score=0.91,                       # numeric score (gen_ai.evaluation.score.value)
        label="pass",                     # categorical label (…score.label)
        explanation="All claims grounded in the retrieved context.",
        evaluator="ragas",                # who produced it
        metadata={"judge_model": "claude-sonnet-4-5"},
    )
```

```python
# python — post-hoc human feedback, hours later, from a webhook (by run_id)
def on_thumbs_down(run_id: str, comment: str) -> None:
    record_feedback(
        run_id=run_id,                    # the run this judges — minted earlier by the SDK
        name="user_satisfaction",
        label="thumbs_down",
        score=0.0,
        comment=comment,
        source="human",                   # vs source="auto" for evals
    )
```

```typescript
// typescript (parity sketch)
import { telemetry } from '@agentforge/sdk';
import { recordEvaluation, recordFeedback } from '@agentforge/sdk-eval';

await telemetry.agentRun('rag-answerer', { version: '3.0.0' }, async (run) => {
  recordEvaluation({ name: 'faithfulness', score: 0.91, label: 'pass',
                     explanation: '…', evaluator: 'ragas' });
});

// later, from a webhook:
recordFeedback({ runId, name: 'user_satisfaction', label: 'thumbs_down',
                 score: 0, comment, source: 'human' });
```

`record_evaluation` (no `run_id`) attaches to the **current** run via the ambient
`TelemetryContext` (feat-002). `record_feedback` (explicit `run_id`) attaches to a
**past** run by id — that is the only structural difference between the two (see §4.3).

### 4.2 Public API / contract

```python
# forgesight_eval/api.py — experimental (rides locked feat-007 event bus + feat-001 model)
from typing import Mapping

def record_evaluation(
    name: str,                            # → gen_ai.evaluation.name  (e.g. "faithfulness")
    *,
    score: float | None = None,           # → gen_ai.evaluation.score.value
    label: str | None = None,             # → gen_ai.evaluation.score.label  (e.g. "pass")
    explanation: str | None = None,       # → gen_ai.evaluation.explanation
    evaluator: str | None = None,         # producer (ragas / deepeval / a judge model)
    run_id: str | None = None,            # None → current run (ambient context)
    metadata: Mapping[str, object] | None = None,
) -> None: ...

def record_feedback(
    name: str,                            # the feedback dimension (e.g. "user_satisfaction")
    *,
    run_id: str,                          # REQUIRED — feedback is always post-hoc, by id
    score: float | None = None,
    label: str | None = None,
    comment: str | None = None,           # free-text human comment
    source: str = "human",               # "human" | "auto"
    metadata: Mapping[str, object] | None = None,
) -> None: ...
```

```python
# forgesight_eval/model.py — experimental
@dataclass(frozen=True, slots=True)
class EvaluationResult:
    name: str
    run_id: str
    score: float | None = None
    label: str | None = None
    explanation: str | None = None
    evaluator: str | None = None
    source: str = "auto"                  # "auto" (eval) | "human" (feedback)
    realtime: bool = True                 # True if attached during the run, else post-hoc
    metadata: Mapping[str, object] = field(default_factory=dict)
```

At least one of `score` / `label` must be set (a result with neither is meaningless;
enforced). Both calls produce an `EvaluationResult`, publish it as an
`EVALUATION_RECORDED` lifecycle event on the **locked** feat-007 event bus, and enqueue
it as a record on the **locked** feat-003 pipeline. The new `EVALUATION_RECORDED`
event kind extends the open lifecycle-event set (FR-8 says the set is open), so it
needs no SPI change. The two module functions are **experimental** within 0.x; the bus
and pipeline they ride are locked.

### 4.3 Internal mechanics

```
record_evaluation(name, score, …)                record_feedback(name, run_id=…, …)
   │  run_id ← arg or current_run() (feat-002)      │  run_id ← required arg
   │  realtime = True                               │  realtime = False; source default "human"
   ▼                                                ▼
build EvaluationResult ───────────────────────────────────────────────────┐
   │                                                                       │
   ├── publish EVALUATION_RECORDED event → feat-007 listeners (isolated)   │
   └── enqueue eval Record → feat-003 pipeline → interceptors → fan-out ───┘
                                          │
                                          ▼  feat-004 OTel mapping
                  span/event with gen_ai.evaluation.name / .score.value /
                  .score.label / .explanation, parented to the run's trace
```

**Real-time eval vs post-hoc feedback — the one real difference.**

- A **real-time eval** runs *during* the run. It has a live `TelemetryContext`, so it
  attaches as a **child span/event under the run's still-open trace** — it nests in the
  same trace tree as the LLM and tool calls it judges. `realtime=True`.
- **Post-hoc feedback** arrives after the run's trace has closed (minutes to days
  later). There is no live span to parent under, so it is emitted as a **standalone
  record carrying the `run_id`** (and, where the backend supports it, the original
  `trace_id` resolved from the run record) so the backend re-associates it. This is why
  `record_feedback` *requires* `run_id` and `record_evaluation` does not.

Both shapes carry the same `gen_ai.evaluation.*` attributes, so a backend treats them
uniformly — the only signal of which is which is `source` (`auto`/`human`) and an
`forgesight.evaluation.realtime` extension attribute.

**Backend translation (P4 — emit once, land everywhere).** Because the canonical wire
format is the OTel `gen_ai.evaluation.*` shape, the existing exporters translate it
without new code in this feature:

- `forgesight-langfuse` maps an `EvaluationResult` to a Langfuse **score** on the
  trace identified by `run_id` (numeric `score.value` → score value; `score.label` →
  string score; `explanation`/`comment` → comment).
- `forgesight-otel` / OTLP-native backends (Phoenix) emit the
  `gen_ai.evaluation.*` event/span; Phoenix surfaces it as an **evaluation** on the
  run's trace.

**Non-blocking (P6/NFR-2).** Neither call performs network I/O; both build a record and
enqueue. A backend that can't accept a score never stalls the agent or the feedback
webhook.

**Content & PII.** `explanation` / `comment` are free text and may contain sensitive
content; they pass through the **same interceptor chain** (feat-008 redaction +
content-capture gate, P7) as any other record before export — eval text is not exempt
from the secure-by-default posture.

### 4.4 Module packaging

- **`forgesight-eval`** is a new opt-in integration package (P2). It holds the two
  module functions, the `EvaluationResult` model, the `EVALUATION_RECORDED` event kind,
  and the record→`gen_ai.evaluation.*` mapping helper. It depends only on `-api` and
  `-core` — **no vendor SDK** (P1); the Langfuse/Phoenix translation lives in those
  existing exporter packages, which already depend on `-core`.

```bash
pip install forgesight-eval
```

```yaml
# forgesight.yaml
modules:
  eval:
    enabled: true
```

**Entry-point registration** — the eval module registers its event kind + record
mapper under the SDK's module-load group so feat-010's bootstrap wires it
automatically:

```toml
# forgesight-eval/pyproject.toml
[project.entry-points."forgesight.modules"]
eval = "forgesight_eval:install"
```

No exporter entry point is added — eval results ride the *existing* exporters; the
package only contributes the record type and its OTel mapping.

### 4.5 Configuration

```yaml
modules:
  eval:
    enabled: true              # master switch for eval/feedback emission (default: false)
    emit_as: "span"            # "span" | "event"  — real-time evals as child spans vs events
    capture_explanation: true  # include gen_ai.evaluation.explanation / comment text
                               #   (subject to the content-capture gate + redaction, P7)
    # Optional declarative score schema — validates score/label at the call site.
    score_schema:
      faithfulness:        { type: "numeric", min: 0.0, max: 1.0 }
      user_satisfaction:   { type: "categorical", labels: ["thumbs_up", "thumbs_down"] }
      relevance:           { type: "numeric", min: 0.0, max: 1.0 }
```

**Validation rules.** `enabled` defaults `false` — installing the package emits nothing
until switched on (P2). `emit_as` ∈ `{span, event}` (default `span`, mirroring
otel-semantic-conventions §8 open-Q's leaning). When `score_schema` names a dimension,
a `record_*` call for that name is validated: `numeric` scores must fall in
`[min, max]`; `categorical` labels must be in the declared set — a violation raises at
the call site (fail-fast) so bad scores never reach a backend. Dimensions not in the
schema are accepted unvalidated (open set). `capture_explanation` defaults `true` but
is still gated by the global content-capture switch (P7) — if content capture is off,
explanation/comment text is dropped regardless.

**Env overrides** (feat-010): `FORGESIGHT_EVAL_ENABLED`,
`FORGESIGHT_EVAL_EMIT_AS`, … with kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

Add it later with `pip install forgesight-eval` + `modules.eval.enabled: true`.
No agent-code change is required to *enable* it; the two `record_*` functions are then
importable wherever the agent (or a webhook) wants to attach a score. Removing it is
`pip uninstall` + dropping the YAML block; runs keep emitting structure/cost/governance
telemetry unchanged.

Upgrade safety: the feature rides the **locked** feat-007 event bus and feat-003
pipeline. `EVALUATION_RECORDED` extends the *open* lifecycle-event set, so it is not a
breaking SPI change; new optional fields on `EvaluationResult` can land in a minor bump
behind safe defaults (P5). The two functions are experimental within 0.x — a signature
change is changelog-called-out, but the contracts beneath them do not move.

## 6. Cross-language parity

Identical across Python / TypeScript: the `gen_ai.evaluation.*` mapping, the
`EvaluationResult` schema, the `EVALUATION_RECORDED` event, the real-time-vs-post-hoc
distinction (`record_evaluation` ambient vs `record_feedback` by-id), and the score
schema. Allowed to differ: idiomatic naming (`recordEvaluation` vs `record_evaluation`,
keyword args vs an options object) and the ambient-context mechanism (`contextvars` vs
`AsyncLocalStorage`). Python lands first (0.3); TypeScript on the 0.4 parity line.

## 7. Test strategy

- **Unit:** `record_evaluation` resolves the ambient `run_id` when none is passed and
  errors clearly when there is no current run *and* no `run_id`; `record_feedback`
  requires `run_id`; at-least-one-of `score`/`label` enforced; `score_schema`
  validation (numeric range, categorical membership) raises at the call site.
- **Integration:** a real-time eval lands as a child span under the run's open trace
  with the exact `gen_ai.evaluation.*` attributes (snapshot against the in-memory
  exporter, feat-011); post-hoc feedback lands as a standalone record carrying the
  right `run_id`/`trace_id`; both fan out to two configured exporters.
- **Backend mapping:** assert the Langfuse exporter turns an `EvaluationResult` into a
  trace score and the OTLP path emits the `gen_ai.evaluation.*` event Phoenix reads.
- **Privacy:** with content capture off, `explanation`/`comment` text is absent from
  the exported record (P7).
- **Example agent:** a RAG agent that self-evaluates faithfulness in real time and
  exposes a feedback webhook, used as the headline demo.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Post-hoc feedback arrives after the trace is gone | Emit as a standalone record carrying `run_id` (+ resolved `trace_id`); backends re-associate by id — this is the documented difference between the two calls |
| `gen_ai.evaluation.*` is `Development`-stability upstream and may churn | Isolated behind the feat-004 mapping module + pinned commit (otel-semantic-conventions §4.1); re-pin without touching callers (P5) |
| Eval explanations leak PII | Routed through the same redaction + content-capture gate as all records (P7); `capture_explanation` is gate-subordinate |
| Score schema drift across teams | Optional declarative `score_schema` validates at the call site; unschema'd dimensions stay open |
| Span vs event for real-time evals | `emit_as` config (default `span`); tracks the otel-semantic-conventions §8 open question, switchable without code change |
| Double-counting an eval as both real-time and post-hoc | `source` + `realtime` attributes disambiguate; dedupe is the backend's concern, not the SDK's |

## 9. Out of scope

- **Running the evaluations.** The SDK *records* eval results; it does not ship judges,
  metrics, or an eval harness (Ragas, DeepEval, LLM-as-judge live in the agent or a
  separate library). It is the transport, not the evaluator.
- **An eval/feedback dashboard.** Scores stream to Langfuse / Phoenix / OTLP backends,
  which display them; the SDK builds no UI (requirements §11).
- **Storing or aggregating scores.** The SDK is a client (requirements §11); rollups
  and quality dashboards live in the backend.
- **Online auto-evaluation triggering.** The SDK does not decide *when* to evaluate or
  fire judges on a schedule; the agent or an external orchestrator does.
- **Datasets / experiment management.** Versioned eval datasets and experiment runs are
  a backend (Langfuse/Phoenix) concern, not SDK scope.

## 10. References

- [`../requirements.md`](../requirements.md) — FR-8 (event publishing), §5 (personas)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) — `gen_ai.evaluation.*` mapping (§4.3), span-vs-event open-Q (§8)
- [`../design/architecture.md`](../design/architecture.md) §3 (`EventListener`, pipeline), §7 (lifecycle)
- [`../design/design-principles.md`](../design/design-principles.md) — P4 (OTel-first), P6 (non-blocking), P7 (secure by default)
- feat-007 (event bus), feat-002 (runtime / `run_id` context), feat-008 (interceptors), feat-004 (OTel mapping)
- Related exporters: feat-013 (Langfuse scores), feat-004/OTLP (Phoenix evaluations)
- Roadmap: features [`README.md`](./README.md) — Phase 3 (governance)
- Prior art: Langfuse scores, Arize Phoenix evaluations, Ragas, DeepEval
