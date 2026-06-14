# feat-008: Interceptors (PII redaction, content gating, policy)

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-008 |
| **Title** | Interceptors — PII redaction, content-capture gating, custom policy/audit |
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

The moment an agent's telemetry leaves the process, it carries a liability:
prompts and tool arguments routinely contain secrets and PII — API keys passed
to a tool, a customer's SSN in a support transcript, a bearer token in an HTTP
tool's headers. Ship that to Langfuse or a shared collector and you've created a
data-leak and a compliance incident. Concretely, teams hit:

- "Our `http_request` tool logs `Authorization: Bearer …` into every trace."
- "Legal says prompt/completion content must never leave the EU collector."
- "Compliance needs every record carrying customer data tagged
  `data_class=pii` before export."
- "We must drop any record for a `test-tenant` run so it never pollutes prod
  dashboards."

These are all the same shape: **inspect, mutate, or veto a record on its way to
export**. Without a first-class hook, teams either capture content they
shouldn't (and hope nobody notices), or bolt regex scrubbing onto each exporter
separately — so the redaction is inconsistent across backends and a new exporter
silently ships raw data.

This feature is the `Interceptor` SPI (FR-10): a chain that runs on every record
on the hot path *before the queue*, where each interceptor can return a mutated
record, the same record, or `None` to drop it. It ships the built-ins that make
**secure-by-default** real: a content-capture gate (P7/ADR-0007) and a PII
redaction interceptor.

## 2. Why this belongs in the SDK

- **Secure-by-default is a framework guarantee, not a per-agent chore (P7).**
  ADR-0007 says content is not captured unless explicitly opted in. That promise
  is only credible if the *gate that enforces it* is framework code that runs
  before any exporter can see a record. If each agent had to remember to redact,
  the default would be "leak," and the first forgotten redaction is a breach.
- **One redaction, every backend.** An interceptor runs once on the record path,
  upstream of the fan-out to N exporters (feat-003). Redact in the interceptor
  and the secret is gone from OTel *and* Langfuse *and* the custom sink — by
  construction. Redact per-exporter and you have N chances to get it wrong and a
  guaranteed leak the day someone adds exporter N+1.
- **A uniform veto/mutate point is what governance builds on.** feat-020 (cost
  budgets & policy) is *literally an interceptor* — a budget interceptor reads
  projected cost and vetoes or annotates a record. If the interceptor contract
  is stable framework surface, budgets, audit tagging, and content gating all
  compose on one chain instead of three bespoke mechanisms.
- **Fault isolation must be owned centrally (P6).** A redaction regex with a
  catastrophic-backtracking pattern, or an audit hook that raises on a malformed
  record, must never crash the run or the export worker. The catch-log-count
  guarantee belongs in the framework.
- **The anti-pattern if we leave it out:** divergent, partial redaction per
  team; content captured by default "to debug" and never turned off; security
  holes that surface only in an audit; and feat-020 reinventing a veto path that
  should have been the interceptor chain.

## 3. How consuming agents/teams benefit

- **Before:** to keep secrets out of traces, a team writes a custom wrapper
  around each exporter that regex-scrubs known keys — ~40 lines per exporter,
  duplicated, and the `http_request` `Authorization` header still leaks because
  they forgot that one. **After:** they enable `PIIRedactionInterceptor` with a
  field list in config; every record across every backend is scrubbed once.
- **Before:** content capture is on "for debugging," prompts with PII flow to a
  shared dashboard, and an auditor finds it six months later. **After:**
  `ContentCaptureGate` is in the chain by default and `capture_content` is
  `false` by default — content simply never leaves unless someone deliberately
  opts in, per environment.
- **Add policy without touching agent code.** A team adds an `audit-tagger`
  interceptor (tag `data_class=pii` on any record with a customer-id field) by
  installing a package and adding one line to `interceptors:` — the agent's code
  is unchanged (P2).
- **Defer the decision.** Day 0: no interceptors, structure-only telemetry. Day
  60: GDPR review lands; the team enables redaction + the content gate in config.
  No code change, no redeploy of agent logic.
- **Compose, don't fork.** When the team later adopts cost budgets (feat-020),
  the budget interceptor slots into the *same* chain ahead of export — they
  already understand the mechanism.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python
import forgesight as af
from forgesight_core.interceptors import (
    ContentCaptureGate,      # secure-by-default enforcement (P7/ADR-0007)
    PIIRedactionInterceptor, # regex/key-based field redaction
)

af.configure(
    capture_content=False,   # default; ContentCaptureGate strips content fields
    interceptors=[
        # ContentCaptureGate is prepended automatically; shown here for clarity.
        PIIRedactionInterceptor(
            redact_keys=("api_key", "password", "secret", "token", "authorization", "ssn"),
            redact_patterns=(r"\b\d{3}-\d{2}-\d{4}\b",),   # US SSN in any captured value
        ),
    ],
)

with af.telemetry.agent_run("support-agent") as run:
    run.tool_call(name="http_request", arguments={"headers": {"Authorization": "Bearer sk-..."}})
    # → record reaches the queue with headers.Authorization == "<redacted>"
```

A custom audit/policy interceptor is just the SPI — return the record, a mutated
copy, or `None` to drop:

```python
from forgesight_api import Interceptor, Record

class DropTestTenant:
    """Veto records from synthetic test tenants so they never hit prod dashboards."""
    def intercept(self, record: Record) -> Record | None:
        if record.metadata.get("tenant") == "test-tenant":
            return None                      # dropped (counted), never exported
        return record

af.configure(interceptors=[DropTestTenant()])
```

Or declaratively (preferred), resolved via entry points:

```yaml
# forgesight.yaml
capture_content: false
interceptors:
  - name: content-gate            # built-in; auto-included even if omitted
  - name: pii-redaction
    config:
      redact_keys: ["api_key", "password", "secret", "token", "authorization", "ssn"]
      redact_patterns: ['\b\d{3}-\d{2}-\d{4}\b']
  - name: audit-tagger            # custom, from your package
```

```typescript
// typescript
import * as af from '@agentforge/sdk';
import { Interceptor, Record } from '@agentforge/sdk-api';

class DropTestTenant implements Interceptor {
  intercept(record: Record): Record | null {
    return record.metadata.tenant === 'test-tenant' ? null : record;
  }
}
af.configure({ captureContent: false, interceptors: [new DropTestTenant()] });
```

### 4.2 Public API / contract

```python
# forgesight_api/spi.py — STABLE (declared in feat-001)
from typing import Protocol, runtime_checkable

@runtime_checkable
class Interceptor(Protocol):
    """Mutate / redact / veto a record before export. Runs in registration order
    on the hot path, before the queue. Returning None drops the record (counted).
    intercept MUST NOT raise into the runtime; if it does, the SDK isolates it
    (that interceptor is skipped for the record; the chain continues)."""
    def intercept(self, record: "Record") -> "Record | None": ...
```

```python
# forgesight_core/interceptors/content_gate.py — STABLE built-in
class ContentCaptureGate:
    """Enforces secure-by-default content opt-in (P7/ADR-0007). When
    capture_content is False, strips content fields (prompt/completion messages,
    tool arguments/results, system instructions) from every record before any
    other interceptor or exporter sees them. Always first in the chain."""
    def __init__(self, *, capture_content: bool = False) -> None: ...
    def intercept(self, record: "Record") -> "Record | None": ...


# forgesight_core/interceptors/pii.py — STABLE built-in
class PIIRedactionInterceptor:
    """Key-based + pattern-based redaction of sensitive fields and tool-arg values.

    - redact_keys:  substring-on-key match (case-insensitive). Any field whose
                    key contains one of these (e.g. "customer_ssn" matches "ssn")
                    has its value replaced with `placeholder`. Applied recursively
                    to nested dicts (tool arguments, headers, metadata).
    - redact_patterns: regex applied to stringified captured values; matches are
                    replaced with `placeholder`. Compiled once at construction.
    Key-based redaction takes precedence over pattern matching."""
    def __init__(
        self,
        *,
        redact_keys: tuple[str, ...] = (
            "api_key", "password", "secret", "token", "authorization",
        ),
        redact_patterns: tuple[str, ...] = (),
        placeholder: str = "<redacted>",
    ) -> None: ...
    def intercept(self, record: "Record") -> "Record | None": ...
```

```typescript
// @agentforge/sdk-api — STABLE
export interface Interceptor { intercept(record: Record): Record | null; }

// @agentforge/sdk-core — STABLE built-ins
export class ContentCaptureGate implements Interceptor {
  constructor(opts?: { captureContent?: boolean });
  intercept(record: Record): Record | null;
}
export class PIIRedactionInterceptor implements Interceptor {
  constructor(opts?: { redactKeys?: string[]; redactPatterns?: string[]; placeholder?: string });
  intercept(record: Record): Record | null;
}
```

**Stable:** the `Interceptor` SPI, `ContentCaptureGate`,
`PIIRedactionInterceptor`. **Experimental:** none in 0.1 — the surface is
deliberately small. Custom audit/policy interceptors are user code implementing
the locked SPI.

### 4.3 Internal mechanics

**Where the chain runs.** Interceptors run on the **hot path, after a record is
built and before the bounded queue** — exactly the stage shown in
[`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.1:

```
record produced (hot path)
   │  build immutable Record
   ▼
ContentCaptureGate            ← always first; strips content if capture_content off (P7)
   ▼
interceptor[1] … interceptor[n]   ← registration order; each: Record | None
   │   None ⇒ record dropped, sdk_records_dropped_total++ , chain stops
   ▼
bounded queue → worker → fan-out to exporters (feat-003)
```

Running before the queue is what makes "one redaction, every backend" true: by
the time a record is enqueued it is already redacted/gated, so **every** exporter
downstream sees the safe version. It also keeps redaction off the export worker,
so a backend outage can't delay it.

**Order and the gate.** `ContentCaptureGate` is **always prepended** to the chain
(even if the user lists their own interceptors), so no later interceptor — and no
exporter — can observe content the operator didn't opt into. After the gate,
user/built-in interceptors run in registration order (config list order, then
programmatic). Order matters and is the caller's contract: redact before tag,
veto before redact, etc.

**Drop semantics.** An interceptor returning `None` drops the record: the chain
stops, the record is never enqueued, `sdk_records_dropped_total{reason=intercept}`
is incremented, and (per feat-007) **no lifecycle event fires** for a dropped
record. This is the veto path feat-020's budget kill-switch uses.

**Mutation semantics.** Interceptors should return a new/mutated `Record`;
records are exporter-facing values (architecture §3). The gate and redaction
built-ins return a copy with the sensitive fields replaced — they never mutate
caller-held state.

**Fault isolation (P6).** Each `intercept` call is wrapped:

```
for interceptor in chain:
    try:
        record = interceptor.intercept(record)
    except Exception:
        log via "forgesight.interceptors" (throttled, with run_id)
        increment sdk_interceptor_errors_total{interceptor=…}
        continue          # skip THIS interceptor for THIS record; chain continues
    if record is None:
        drop (counted); stop chain
```

A raising interceptor is **skipped for that record** and the chain continues with
the record as it stood — it never crashes the run or the worker. Note the
deliberate safety choice: a raising interceptor *fails open* (the record
continues, possibly un-redacted for that one interceptor) and is loudly counted,
rather than silently dropping data; the content gate, which fails *closed*, runs
first and is the backstop for the security-critical case.

**Performance.** The chain is O(#interceptors) on the hot path with no I/O,
inside the < 5 ms p99 budget (NFR-1). `redact_patterns` are compiled once at
construction; key matching is a lower-cased substring check. Operators are warned
against pathological regexes (catastrophic backtracking) since they run inline.

### 4.4 Module packaging

- **Lives in `forgesight-core`** (always installed). The `Interceptor` SPI is
  in `forgesight-api` (the locked leaf); the chain runner and both built-ins
  (`ContentCaptureGate`, `PIIRedactionInterceptor`) are in `forgesight-core`.
  No extra install — they're available the moment you `configure()`.

  ```bash
  pip install forgesight        # SPI + built-in interceptors included
  ```

- **Custom interceptors as entry points.** Resolvable by name from config when
  registered under the entry-point group `forgesight.interceptors`:

  ```toml
  # pyproject.toml of the package shipping the interceptor
  [project.entry-points."forgesight.interceptors"]
  audit-tagger = "myorg.telemetry.audit:AuditTagger"
  drop-test-tenant = "myorg.telemetry.policy:DropTestTenant"
  ```

  Or in-process: `@forgesight.register("interceptors", "audit-tagger")`.
  Built-ins register under the same group (`content-gate`, `pii-redaction`) — no
  privileged path. The name is then usable in the `interceptors:` config list.

- **Cross-ref feat-020.** Cost budgets & governance ship a `BudgetInterceptor`
  (in `forgesight-governance`) that implements this same SPI and registers
  under `forgesight.interceptors` — budgets are an interceptor, not a new
  mechanism. See feat-020.

### 4.5 Configuration

```yaml
# forgesight.yaml
capture_content: false           # P7/ADR-0007 default; drives ContentCaptureGate

# The interceptor chain, in execution order. content-gate is always prepended
# (listing it is optional). Each `name` resolves via the
# forgesight.interceptors entry-point group.
interceptors:
  - name: pii-redaction
    config:
      redact_keys:               # substring-on-key, case-insensitive
        - api_key
        - password
        - secret
        - token
        - authorization
        - ssn
      redact_patterns:           # regex over stringified captured values
        - '\b\d{3}-\d{2}-\d{4}\b'        # US SSN
        - '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\b'  # email
      placeholder: "<redacted>"
  - name: audit-tagger           # custom; no config ⇒ defaults
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `capture_content` | `FORGESIGHT_CAPTURE_CONTENT` | `false` | Mirrors OTel's `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. When false, the gate strips all content fields (P7). |
| `interceptors` | — (list; file/kwargs only) | `[]` (+ implicit `content-gate`) | Ordered; each name resolves via `forgesight.interceptors` or fail-fast at `configure()` (feat-010). |
| `interceptors[].config.redact_keys` | — | `("api_key","password","secret","token","authorization")` | Substring match on field key. |
| `interceptors[].config.redact_patterns` | — | `()` | Compiled once; applied to stringified values. |
| `interceptors[].config.placeholder` | — | `"<redacted>"` | Replacement text. |

Validation: `redact_patterns` must compile (a bad regex fails fast at
`configure()`); unknown interceptor names fail fast with the entry-point group
named (feat-010). With `capture_content: false`, redaction patterns over content
values are a no-op (there is no content to scan) — key-based redaction over
structural fields (metadata, tool-arg keys) still applies.

## 5. Plug-and-play & upgrade story

The chain and built-ins are in `forgesight-core` — always installed, nothing
to add at scaffold time. Adding a *custom* interceptor later is the plug-and-play
case: install the package that ships it (or register in-process) and add a line
to `interceptors:` — no agent-code change (P2). Turning on redaction or the gate
is purely config.

Upgrade safety (P5): the `Interceptor` SPI is locked — `intercept(record) ->
Record | None` does not change without a major bump + ADR. The built-ins may gain
optional constructor kwargs with safe defaults in a minor (e.g. a new
`redact_patterns`), never a breaking signature change. A custom interceptor
written against 0.1 survives every minor.

## 6. Cross-language parity

Identical across Python / TypeScript: the `Interceptor` contract,
`intercept → Record | null` (None/null drops), registration-order execution,
the always-first `ContentCaptureGate`, the key+pattern semantics of
`PIIRedactionInterceptor` (key match wins over pattern), running before the
queue, and fault isolation. Allowed to differ: idiomatic naming
(`redact_keys` ↔ `redactKeys`), and the regex engine's exact dialect (both use
the host language's standard regex). Python lands first (0.1).

## 7. Test strategy

- **Unit (gate):** with `capture_content=False`, every content field
  (prompt/completion messages, tool args/results, system instructions) is absent
  from the record reaching the queue; with `True`, content is preserved. The gate
  is always first regardless of how interceptors are ordered in config.
- **Unit (redaction):** key match is case-insensitive and substring
  (`customer_ssn` redacted by `ssn`); nested dicts (tool-arg headers) are
  redacted recursively; pattern match replaces matched substrings; key-based wins
  precedence over pattern.
- **Veto:** returning `None` drops the record, stops the chain, increments
  `sdk_records_dropped_total{reason=intercept}`, and suppresses the lifecycle
  event (feat-007).
- **Fault isolation (headline):** a raising interceptor is skipped for that
  record, is counted in `sdk_interceptor_errors_total`, and neither the run nor
  the chain nor sibling interceptors are affected; the content gate still runs
  even when a later interceptor raises.
- **Order:** interceptors execute in registration order; reordering changes
  observable behaviour (redact-then-tag vs tag-then-redact).
- **Conformance:** `run_interceptor_conformance` (feat-011) — every shipped and
  third-party interceptor runs the same suite (never-raises-into-runtime,
  None-drops, returns-a-record-or-None, idempotent-on-replay). The
  `BudgetInterceptor` (feat-020) passes this same suite.
- **Performance:** the chain stays within the < 5 ms p99 hot-path budget (NFR-1).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| A pathological redaction regex (catastrophic backtracking) blocks the hot path | Patterns compiled + documented; operators warned; consider a per-pattern timeout later. |
| A raising interceptor "fails open" and lets an un-redacted record through | Deliberate: fail-open + loud count beats silent data loss; the content gate fails *closed* and runs first as the security backstop. |
| Over-redaction (a too-broad key like `token` hides a useful `request_token_count`) | Substring match is documented; defaults are conservative; callers tune `redact_keys`. |
| PII the regex doesn't catch | Redaction is best-effort defence-in-depth, not a guarantee; the real guarantee is `capture_content=False` (content never captured at all). |
| Should interceptors run on the worker instead of the hot path? | No — running before the queue is what makes redaction backend-uniform and outage-independent; the chain is O(n) no-I/O. |

## 9. Out of scope

- **ML-based / NER PII detection.** 0.1 ships key + regex redaction. A pluggable
  detector is a custom interceptor a team can write against the SPI; the SDK does
  not bundle an ML model (footprint, P1).
- **Cost budgets / kill-switch.** That's feat-020 — a `BudgetInterceptor` built
  *on* this SPI, shipped in `forgesight-governance`, not here.
- **Per-exporter redaction policy.** Interceptors run once, before fan-out, by
  design. A backend that needs a *different* view is the rare case and out of
  scope for 0.1 (it would defeat "one redaction, every backend").
- **Reversible / tokenised redaction (format-preserving encryption).** The
  built-in replaces wholesale with a placeholder; reversible schemes are a custom
  interceptor concern.
- **Async interceptors.** The chain is synchronous on the hot path; an
  interceptor that needs I/O (a live PII-detection API) is an anti-pattern there
  — do that work in an exporter or a listener, not the hot path.

## 10. References

- [`requirements.md`](../requirements.md) FR-10, P7
- [`architecture.md`](../design/architecture.md) §3 (Interceptor concept), §4 (SPI), §7 (lifecycle), §8 (failure modes)
- [`design-principles.md`](../design/design-principles.md) P6, P7, P8, P10
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.1, §4.4 (chain placement + isolation)
- [`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (content is Opt-In; gate enforces it)
- ADR-0007 (content capture opt-in)
- feat-001 (the `Interceptor` SPI + `Record`), feat-002 (the runtime + hot path)
- feat-020 (cost budgets & governance — a `BudgetInterceptor` built on this SPI)
- feat-011 (`run_interceptor_conformance`)
