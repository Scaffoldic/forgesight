# feat-009: Error & exception tracking

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-009 |
| **Title** | Error & exception tracking (type/message/stack/code; span status + `error.type`) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` |
| **Depends on** | feat-001, feat-002 |
| **Blocks** | none |

---

## 1. Why this feature

When an agent run breaks, the first question is always "broke *how*?" — and the
telemetry has to answer it without a re-run. The failure modes are specific and
common:

- An LLM call 429s after the provider rate-limits you; the run dies three steps
  in. The dashboard shows a run that "stopped" — but not *why*.
- A tool raises `ConnectionError` against a flaky downstream; you need the
  exception type and stack to know which dependency to page.
- A provider returns `finish_reason=content_filter`; the model didn't error, but
  the run can't proceed and you need that recorded distinctly from a crash.
- A bad API key throws `AuthenticationError` with a provider error code; you need
  that code surfaced, not buried in a generic "error."

Without first-class error capture, a failed run is a hole in the trace: a span
that simply ends, no status, no type, no stack. Operators fall back to
correlating timestamps against application logs by hand — the exact print-debug
in front of an angry user this whole SDK exists to prevent. And there's a subtler
trap: an observability layer that **swallows** the caller's exception to "record
it cleanly" silently changes the agent's control flow, turning a crash into a
fake success. That's worse than no telemetry.

This feature is FR-7: on any failed operation, capture the exception **type,
message, stack trace, and error code**; set the span status to error and the
stable `error.type` attribute; mark the run `RunStatus.ERROR` and fire
`RUN_FAILED` — and **re-raise**, never swallow.

## 2. Why this belongs in the SDK

- **Uniform error shape across every agent and backend.** If the SDK captures
  `error.type` (a *stable* OTel attribute, unlike the `Development`-stability
  GenAI attrs) the same way for every run, then "show me failure rate by
  exception type across the fleet" is one query. If each agent records errors its
  own way, errors are incomparable and `agent_failures_total` (FR-6) is
  meaningless. Error capture has to be framework-owned for the metric to mean
  anything.
- **The re-raise contract is a correctness invariant, not a feature.** "Record
  then re-raise; never swallow the caller's exception" (FR-7 acceptance) is the
  kind of guarantee that *must* live in one place. If every agent author had to
  remember to re-raise after recording, some wouldn't, and their agents would
  silently mark failures as success. The SDK's context managers enforce it so no
  caller can get it wrong.
- **Error capture has to compose with the rest of the pipeline.** A recorded
  error must run through interceptors (so a stack trace with a secret in it gets
  redacted — feat-008), set the run status that the cost/finish logic reads, and
  trigger the `RUN_FAILED` lifecycle event (feat-007). Wiring those together is
  framework work; an agent-local try/except can't.
- **Sensitive data in stack traces is a real leak vector.** Exception messages
  and locals routinely contain tokens and PII. The SDK must route captured error
  content through the same redaction path as everything else (P7); a per-agent
  error logger won't.
- **The anti-pattern if we leave it out:** every agent re-implements
  record-and-reraise, half of them swallow exceptions by accident, `error.type`
  is set inconsistently (or not at all), and fleet-wide failure analysis is
  impossible.

## 3. How consuming agents/teams benefit

- **Before:** a tool raises; the agent author wraps the run in a try/except,
  logs `str(e)`, and re-raises — but forgets the stack and the error code, so the
  trace shows "error" with no type and the on-call engineer greps app logs by
  timestamp. ~15 lines per agent, inconsistent. **After:** the SDK records type +
  message + stack + code and sets `error.type` automatically; the failing span is
  red in every OTLP backend with the exception class right on it. Zero lines.
- **Before:** an observability wrapper swallows exceptions to "keep the run
  clean," and a rate-limit failure is recorded as `RUN_COMPLETED`. The team
  ships a broken agent thinking it works. **After:** the SDK records *and
  re-raises* — the agent's own error handling still runs, the status is
  `ERROR`, and `RUN_FAILED` fires. Telemetry never changes control flow.
- **Distinguish "crashed" from "couldn't proceed" for free.** A
  `finish_reason=content_filter` or an exhausted error-streak is recorded with
  its own status/reason, not conflated with an exception crash — so dashboards
  separate "the agent threw" from "the model refused."
- **Fleet failure analysis on day one.** Because every agent sets `error.type`
  the same way, `sum by (error.type) (rate(agent_failures_total))` works across
  every team's agents with no coordination.
- **Stack traces are redacted by default.** Captured error content flows through
  the interceptor chain (feat-008), so a token in an exception message is scrubbed
  before export without the agent author doing anything.

## 4. Feature specifications

### 4.1 User-facing experience

The default: error capture is **automatic** — the context managers record and
re-raise. The agent author writes nothing extra.

```python
# python
import forgesight as af

with af.telemetry.agent_run("payments-agent") as run:
    with run.llm_call(provider="anthropic", request_model="claude-sonnet-4-5"):
        raise RateLimitError("429 from provider", code="rate_limited")
    # On the raise, the SDK:
    #   • records error.type="RateLimitError", message, stack, error.code="rate_limited"
    #     on the llm_call span; sets span status = ERROR
    #   • RE-RAISES — the caller's exception propagates unchanged
# The agent_run context manager, seeing the run exit via exception:
#   • sets run status = RunStatus.ERROR
#   • emits RUN_FAILED (feat-007) with the error record as payload
#   • RE-RAISES — the exception reaches your code
```

So the caller's own handling still runs — the SDK observes, it does not intercept
control flow:

```python
try:
    with af.telemetry.agent_run("payments-agent") as run:
        ...                       # something raises deep inside
except RateLimitError:
    backoff_and_retry()           # YOUR handler still fires; the SDK did not swallow it
```

Manual recording (for code paths not wrapped in a context manager, e.g. an error
caught and handled without re-raising):

```python
with af.telemetry.agent_run("batch-agent") as run:
    for item in batch:
        try:
            run.tool_call(name="enrich", arguments={"id": item.id})
        except EnrichError as e:
            run.record_error(e, code="enrich_failed")   # records, does NOT re-raise
            continue                                     # you chose to keep going
```

```typescript
// typescript
import * as af from '@agentforge/sdk';

await af.telemetry.agentRun('payments-agent', async (run) => {
  await run.llmCall({ provider: 'anthropic', requestModel: 'claude-sonnet-4-5' }, async () => {
    throw new RateLimitError('429 from provider', { code: 'rate_limited' });
  });
}); // records error.type/message/stack/code, sets status ERROR, emits RUN_FAILED, re-throws
```

### 4.2 Public API / contract

```python
# forgesight_api/model.py — STABLE (extends feat-001)
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Captured on any failed operation (FR-7). Carried on the record; mapped to
    error.type (stable OTel attr) + span status; redacted via interceptors (P7)."""
    error_type: str               # exception class name → error.type (OTel stable attr)
    message: str                  # str(exc); redactable content
    stacktrace: str | None        # formatted traceback, depth-limited (config)
    code: str | None = None       # provider/domain error code when present
    # RunStatus.ERROR is set on the owning AgentRun (feat-001 enum)
```

```python
# forgesight_core/runtime.py — instrumentation surface (feat-002)
class RunHandle:
    def record_error(
        self,
        exc: BaseException,
        *,
        code: str | None = None,
    ) -> None:
        """Capture exc (type/message/stack/code) onto the current span, set its
        status to ERROR and error.type. Does NOT re-raise — for caught-and-handled
        paths. The context managers call this internally then re-raise."""

# Context managers (agent_run / step / llm_call / tool_call / mcp_call) record on
# __exit__ when leaving via an exception, then RE-RAISE (never swallow). — STABLE
```

```typescript
// @agentforge/sdk-api — STABLE
export interface ErrorInfo {
  readonly errorType: string;     // → error.type
  readonly message: string;
  readonly stacktrace?: string;
  readonly code?: string;
}
// run.recordError(err, { code }) records without re-throwing; the wrappers
// record-then-rethrow on a thrown callback.
```

**Stable:** `ErrorInfo`, `RunStatus.ERROR` (feat-001), `record_error`, the
record-then-re-raise behaviour of every context manager, and the mapping to
`error.type` + span status. **Experimental:** the exact stacktrace formatting and
default depth (a tunable, not a contract).

### 4.3 Internal mechanics

**Capture path.** When an instrumented operation fails — either a context
manager exits with an exception, or `record_error` is called — the runtime:

1. Builds an `ErrorInfo`: `error_type = type(exc).__name__`, `message =
   str(exc)`, `stacktrace = traceback up to stack_capture_depth`, `code` from the
   kwarg or a recognised provider-exception attribute.
2. Sets the active span's **status to ERROR** and the **`error.type` attribute**
   — `error.type` is a *stable* attribute from the main OTel semantic
   conventions (not the `Development`-stability GenAI set), so it's safe to lock
   on ([`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md)
   §4.3).
3. Attaches `ErrorInfo` to the record so it flows through the **interceptor
   chain** (feat-008) — a stack trace or message with a secret is redacted before
   export (P7), exactly like any other content.

```
operation raises
   │
   ├─ build ErrorInfo(type, message, stack[:depth], code)
   ├─ span.set_status(ERROR); span.set_attribute("error.type", <class>)
   ├─ attach ErrorInfo to the record  ──▶ interceptors (redact) ──▶ queue (feat-003)
   │
   └─ RE-RAISE   ← the exception continues to the caller, unchanged
```

**Run-level rollup.** When the *run's* context manager exits via an exception (or
`record_error` is called at run scope), the run's status is set to
`RunStatus.ERROR` and **`RUN_FAILED`** is emitted (feat-007) with the `ErrorInfo`
record as payload — distinct from `RUN_COMPLETED`. `agent_failures_total` (FR-6,
feat-005) increments off this status.

**The re-raise contract (the headline behaviour).** The SDK's context managers
**record then re-raise**. They do not swallow the caller's exception. Concretely,
`__exit__` returns falsy after recording, so Python re-raises; the TS wrappers
re-throw after recording. This is FR-7's acceptance criterion: *"…without
swallowing the exception from the caller (unless the caller is using a context
manager that re-raises)"* — and our context managers are exactly that re-raising
kind. `record_error` is the *opt-out* for code that has caught and handled an
error itself and explicitly does not want it re-raised.

**Error vs finish-reason interplay.** Not every "the run can't proceed" is an
exception:

- An **LLM `finish_reason`** like `content_filter` / `length` is recorded as a
  finish reason on the LLM call (`gen_ai.response.finish_reasons`), *not* as
  `error.type`. The model didn't throw. Whether that fails the *run* is the
  agent/strategy's policy (feat-002), not this feature's — but if the agent
  decides to fail, that surfaces as `RunStatus.ERROR` + `RUN_FAILED` like any
  other run failure.
- An **error streak** (repeated tool/LLM failures the agent retried past a
  threshold) is the agent's own control decision; when it terminates the run, the
  terminal status is `RunStatus.ERROR` carrying the *last* `ErrorInfo`, and the
  per-attempt errors are recorded on their own spans. So a dashboard shows N red
  leaf spans under one red run, not one opaque failure.
- Distinct terminal statuses stay distinct: `BUDGET_EXCEEDED` (feat-020) and
  `GUARDRAIL` are *not* `ERROR` and do not set `error.type` — they're recorded as
  their own `RunStatus`, so "the agent crashed" never gets conflated with "policy
  stopped it."

**Stack capture depth + redaction interplay.** `stack_capture_depth` bounds how
many frames are formatted (cost + leak control). The formatted stack and message
are *content*: they pass through the interceptor chain, so `PIIRedactionInterceptor`
(feat-008) scrubs secrets in them, and `ContentCaptureGate` governs whether the
*message body* is captured at all when content capture is off — `error.type` and
`code` (structure, not content) are always captured; the human-readable message
and full stack follow the content-capture rules.

### 4.4 Module packaging

- **Lives in `forgesight-core`** (always installed). Error capture is part of
  the instrumentation runtime (feat-002) — the context managers and
  `record_error` are core. `ErrorInfo` is in `forgesight-api` (the locked
  leaf). The `error.type` attribute name comes from the stable OTel semconv
  constants in `-api`. No extra install.

  ```bash
  pip install forgesight        # error capture is on by default
  ```

- **No entry-point group of its own.** Error tracking is not an extension point —
  it's built-in runtime behaviour. It *composes with* the existing groups:
  redaction via `forgesight.interceptors` (feat-008) and `RUN_FAILED`
  delivery via `forgesight.listeners` (feat-007).

### 4.5 Configuration

```yaml
# forgesight.yaml
errors:
  stack_capture_depth: 20        # frames formatted into ErrorInfo.stacktrace; 0 = type+message only
  capture_stacktrace: true       # set false to record type/message/code but no stack (perf/PII)
# Redaction of error message + stack is governed by the interceptor chain (feat-008)
# and capture_content (P7) — there are no error-specific redaction keys here.
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `errors.stack_capture_depth` | `FORGESIGHT_STACK_CAPTURE_DEPTH` | `20` | Max frames formatted; bounds cost + leak surface (P8 — named, defaulted). |
| `errors.capture_stacktrace` | `FORGESIGHT_CAPTURE_STACKTRACE` | `true` | When false, `ErrorInfo.stacktrace` is `None`; type/message/code still captured. |

Redaction interplay: the captured `message`/`stacktrace` are content and pass
through the interceptor chain — to scrub secrets in them, configure
`pii-redaction` (feat-008 §4.5); to suppress message bodies entirely, set
`capture_content: false` (P7). `error.type` and `code` are structural and always
captured.

## 5. Plug-and-play & upgrade story

Error tracking is in `forgesight-core` — always installed, on by default,
nothing to add at scaffold time. It needs no enabling. Redaction of error content
is added later exactly as any interceptor is (feat-008 §5); `RUN_FAILED`
reactions are added as listeners (feat-007 §5).

Upgrade safety (P5): `ErrorInfo`, `record_error`, the `error.type` + span-status
mapping, and the record-then-re-raise contract are locked. `ErrorInfo` may gain
optional fields with safe defaults in a minor; the stacktrace *format* is
explicitly not a contract (experimental). A consumer relying on "the SDK never
swallows my exception" keeps that guarantee across all 0.x.

## 6. Cross-language parity

Identical across Python / TypeScript: the `ErrorInfo` fields, the mapping to
`error.type` + error span status, the record-then-re-raise (re-throw) contract,
`record_error`'s no-re-raise semantics, the run-level `RUN_FAILED` rollup, and
the finish-reason-vs-error distinction. Allowed to differ: idiomatic naming
(`record_error` ↔ `recordError`, `error_type` ↔ `errorType`), the traceback
format (Python `traceback` vs JS `Error.stack`), and the exception base type
(`BaseException` vs `Error`). Python lands first (0.1).

## 7. Test strategy

- **Re-raise (headline):** an exception raised inside `agent_run` / `step` /
  `llm_call` / `tool_call` / `mcp_call` propagates to the caller unchanged; the
  caller's `except` fires. Assert the SDK never swallows.
- **Capture completeness:** the failing span carries `error.type` = the exception
  class, status = ERROR, and the record has message + stack (within depth) +
  code.
- **Run rollup:** a run that exits via exception has `RunStatus.ERROR`, emits
  `RUN_FAILED` (not `RUN_COMPLETED`), and increments `agent_failures_total`.
- **`record_error`:** records without re-raising; the loop continues.
- **Finish-reason vs error:** a `content_filter` finish reason sets the finish
  reason, not `error.type`; `BUDGET_EXCEEDED`/`GUARDRAIL` statuses do not set
  `error.type`.
- **Depth + redaction:** `stack_capture_depth=0` yields type+message only;
  `capture_stacktrace=false` yields `stacktrace=None`; a secret in an exception
  message is redacted by `pii-redaction` before export; `capture_content=false`
  suppresses the message body but keeps `error.type`/`code`.
- **Error streak:** N failed attempts produce N red leaf spans under one
  `ERROR` run carrying the last `ErrorInfo`.
- **Example:** an agent whose tool flaps, asserting the trace shows typed,
  redacted errors and a `RUN_FAILED` event.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Stack traces leak secrets/PII | Routed through the interceptor chain (feat-008); `capture_content` governs the message body; `error.type`/`code` are structural and safe. |
| Deep stacks cost time/memory on the hot path | `stack_capture_depth` bound (default 20); `capture_stacktrace: false` to skip entirely. |
| A caller *wants* the SDK to swallow (record-and-continue) | That's `record_error` — explicit opt-out; the default never swallows. |
| `error.type` cardinality blows up metrics | `error.type` is the exception *class*, not the message; cardinality is bounded by exception types. |
| Mapping provider error codes consistently | `code` is captured verbatim when present (kwarg or recognised exception attr); the SDK does not normalise provider codes in 0.1. |

## 9. Out of scope

- **Error grouping / fingerprinting / dedup** (Sentry-style issue grouping). The
  SDK emits typed errors; grouping is the backend's job (requirements §11 — no
  dashboard).
- **Alerting on errors.** Metrics + `RUN_FAILED` events are emitted; alerts are
  configured in the user's stack (use feat-007 for a Slack-on-failure listener).
- **Automatic retry / recovery.** Recording an error does not retry it — retry is
  the agent/strategy's policy (feat-002), not telemetry's.
- **Normalising provider error codes** into a canonical taxonomy. `code` is
  captured as given.
- **Capturing local variables / frame values** in the stack. Only formatted
  frames are captured (leak + cost control).

## 10. References

- [`requirements.md`](../requirements.md) FR-7, FR-1 (`RunStatus`), FR-6 (`agent_failures_total`)
- [`architecture.md`](../design/architecture.md) §4.1 (`RunStatus`), §7 (lifecycle), §8 (failure modes)
- [`design-principles.md`](../design/design-principles.md) P6, P7, P8
- [`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (`error.type` is a stable attr + span status)
- feat-001 (`RunStatus`, the `Record` model), feat-002 (the context managers that record + re-raise)
- feat-007 (`RUN_FAILED` lifecycle event), feat-008 (redaction of error content), feat-005 (`agent_failures_total`)
- OpenTelemetry trace semantic conventions — recording exceptions + `error.type`
