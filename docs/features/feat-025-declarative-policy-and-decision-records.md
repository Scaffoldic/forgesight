# feat-025: Declarative policy & decision records

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-025 |
| **Title** | Declarative policy & decision records — a version-controlled policy DSL, a `PolicyEngine` evaluated at runtime, allow/deny decisions emitted as telemetry |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-20 |
| **Target version** | 0.5 / 0.6 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-policy` |
| **Depends on** | feat-008 (interceptors), feat-020 (cost budgets & governance — reuse), feat-006 (cost/spend) |
| **Blocks** | none |

---

## 1. Why this feature

feat-020 gave the SDK the thing FinOps asked for: the ability to *stop* spend and
*veto* a call before it happens, on the interceptor chain (feat-008). It ships three
**code** interceptors — `BudgetInterceptor`, `PolicyInterceptor`, `KillSwitch` — and the
one sanctioned way to halt a run, `GovernanceSignal`. That works. But the policy a
governance-minded team actually has to defend lives in Python: `PolicyRule(match=…,
action=…, models=…)` objects assembled in code, or a `governance.policies.rules:` YAML
fragment that is a thin transcription of those objects. The gap that hits teams now is
not "can we deny?" — it is **"can a person who is not the agent author read, review, and
version the rules, and can they prove a given call was allowed or denied, and why?"**

Concrete scenarios that hit teams today:

- A platform team must guarantee that **prod agents only call GA models** — never a
  `*-preview` or an un-priced experimental model. feat-020 can express the `deny`, but
  the allow-list is a Python literal three packages deep. A compliance reviewer cannot
  read it, cannot diff it across releases, and cannot tell whether `environment=prod`
  is actually covered or was quietly dropped in a refactor.
- A security reviewer signing off on a release needs the **model and tool allow-list in
  one version-controlled file**, with the posture stated up front (deny anything not
  listed), not reconstructed by reading interceptor wiring. "Show me, in git, exactly
  which models and tools each team is allowed to use" has no answer today.
- A run is denied at 3 a.m. The on-call sees the run went `GUARDRAIL` and the call never
  happened — but **the reason is not in the telemetry**. Why was it denied? Which rule?
  Was it the model, the region, the spend cap? `PolicyDenied` carries the reason
  in-process, but nothing in the exported trace says "policy X denied this for reason Y."
  The deny is invisible to the very observability stack the SDK exists to feed.
- An auditor asks "for last quarter, every call that was *allowed under a spend
  obligation* and every call that was *denied*, with the matched rule." Today that is a
  log-grep at best — the decision is a control-flow event, not a first-class, queryable
  signal.
- A team wants `amount_usd <= 50` and "only EU regions for `data_class=pii`" as
  *declared policy*, reviewed like code. feat-020's caps live under `governance.budgets`
  and its policy under `governance.policies` as two separate code-interceptor surfaces;
  there is no single declarative document a reader can hold that says "here is what is
  allowed, here is what is denied, here is the spend ceiling."

feat-020 made governance *possible*. This feature makes it **readable, reviewable, and
observable**: a declarative, version-controlled policy evaluated at runtime on the same
interceptor chain, where **every evaluation emits an allow/deny decision record into the
telemetry stream** — so a deny is not just enforced, it is *seen*.

## 2. Why this belongs in the SDK

- **The decision point is already framework-owned, and only the framework can emit the
  decision as telemetry.** feat-020 established that governance rides the **locked**
  `Interceptor` SPI (feat-008) at `model.call` / `tool.call` / spend, and that a halt is
  the one sanctioned `GovernanceSignal` exception (the single case the pipeline does
  *not* swallow). A declarative policy is the *same* hook with a *readable* rule source
  in front of it. Crucially, the SDK is the only component sitting on every call as it
  happens *and* owning the telemetry pipeline — so it is the only place that can turn an
  allow/deny into a `forgesight.policy.*` event on the same trace as the call it gates.
  A side-car that evaluates policy after export cannot stop the call; one that evaluates
  before export but outside the SDK cannot put the decision on the call's span.
- **Uniformity is the whole point of governance (the feat-020 argument, extended).** If
  `team=growth`'s allow-list means one thing in agent A's code and another in agent B's,
  a reviewer cannot reason about the fleet. feat-020 made the *enforcement* uniform; a
  single declared policy document makes the *rules themselves* uniform and reviewable —
  one file, one posture, one diff. Leaving the DSL to each agent reintroduces exactly
  the drift feat-020 removed.
- **It reuses feat-020 wholesale instead of reinventing it.** The spend path is
  `BudgetInterceptor` / `BudgetCap` (feat-006 pricing underneath); a deny raises the
  existing `GovernanceSignal` to terminal `RunStatus.GUARDRAIL`; the engine registers
  under the existing `forgesight.interceptors` entry-point group. This feature adds a
  *declarative front end and a decision-record back end* to feat-020's mechanism, not a
  second governance stack.
- **Decision records are pure observability — the SDK's core job (FR-10, P4).** Every
  allow/deny becomes a namespaced `forgesight.policy.*` event/attribute on the call's
  span, so "every deny shows up in telemetry with the reason" is true by construction.
  When the audit feature is present, the same decision flows into the audit trail. The
  SDK already owns the span and the pipeline; emitting the decision is a stamp, not a
  new data plane.
- **The anti-pattern if we don't:** the allow-list stays buried in code, compliance
  reviews are archaeology, denies are silent in the trace, and every team that wants a
  *readable* policy writes its own YAML-to-`PolicyRule` loader — re-deriving the parser,
  the allow-listing posture, and the decision logging feat-020 should have anchored.

This is squarely **FR-10 (interception / policy)** with **FR-9 (cost/spend)** under the
spend path, the FinOps / governance persona (requirements §5), and it extends roadmap
**Phase 3 (governance)** into the 0.5/0.6 platform line.

## 3. How consuming agents/teams benefit

**Before.** Governance rules live in code or in a `governance.policies.rules:` fragment
that mirrors code objects. A compliance reviewer cannot read the allow-list; a security
sign-off means tracing interceptor wiring by hand. When a run is denied, the trace shows
`GUARDRAIL` and a missing call — the *reason* is in a `PolicyDenied` that never reached
the backend. Proving "every prod call used a GA model" is a manual reconstruction.

**After.**

- **Day 0 — one readable policy file, reviewed like code.** A team writes `policy.yaml`:
  a `version`, a `defaults` posture (deny-unknown-models, deny-unknown-tools), and
  `rules` with `when` / `allow` / `deny` / `allow_with`, including numeric caps
  (`amount_usd <= 50`). A compliance reviewer reads it top to bottom and approves it in a
  pull request — no Python, no interceptor archaeology. The agent author writes *zero*
  policy code.
- **Day 7 — runtime enforcement, unchanged mechanism.** The `native` engine is wired as
  an interceptor (feat-008); at every `model.call` / `tool.call` / `spend` it builds a
  `PolicyContext` and evaluates the declared policy to a `Decision`. A deny raises the
  existing `GovernanceSignal` (feat-020) and the run terminates `GUARDRAIL` — the same
  halt teams already understand, now driven by a file a reviewer signed off on.
- **Day 14 — every decision is observable.** Each evaluation emits a
  `forgesight.policy.decision` event on the call's span: `effect=allow|deny`, the matched
  `rule`, the `reason`, any `obligations`. "Show me every deny last quarter with the
  reason" is now a backend query, not a log-grep. The on-call reads the deny *and its
  reason* straight off the trace.
- **Day 30 — spend as policy, reusing budgets.** `allow_with: { amount_usd: { max: 50 }}`
  routes through `BudgetInterceptor` / `BudgetCap` (feat-020) over feat-006 pricing — the
  declared cap is enforced by the same projected-cost machinery, and the obligation shows
  up in the decision record.
- **The win:** the governance the team already enabled in feat-020 becomes *legible* (a
  version-controlled file a non-author can review) and *auditable* (every allow/deny in
  telemetry with its reason), by declaring policy *once* in YAML instead of assembling
  rule objects in code and hoping the trace explains the deny.

## 4. Feature specifications

### 4.1 User-facing experience

```yaml
# policy.yaml — the declared, version-controlled policy (one reviewable document)
version: 1
defaults:
  # Allow-listing posture: anything not explicitly allowed is denied.
  models: deny-unknown          # deny-unknown | allow-unknown
  tools:  deny-unknown
  on_engine_error: deny         # fail-closed (see §8 open question)

rules:
  # 1. prod may only call GA models — the headline allow-list, readable by a reviewer.
  - when: { environment: prod }
    allow:
      models: ["claude-sonnet-4-5", "claude-haiku-*", "gpt-4.1", "gpt-4.1-mini"]
    deny:
      models: ["*-preview", "*-experimental"]     # never an un-priced/preview model in prod
      reason: "prod may only call GA models"

  # 2. growth: cheap models only, and a per-call spend ceiling (reuses budgets, feat-020).
  - when: { team: growth }
    allow_with:
      models: ["claude-haiku-*", "gpt-4.1-mini"]
      amount_usd: { max: 50 }                       # → BudgetCap, enforced via feat-006 pricing

  # 3. PII data must stay in EU regions; deny everything else for that data-class.
  - when: { data_class: pii }
    allow:
      regions: ["eu-west-1", "eu-central-1"]
    obligations: ["require_redaction"]              # a non-content obligation (see §9)

  # 4. tool allow-list for the payments team.
  - when: { team: payments }
    allow:
      tools: ["lookup_invoice", "post_ledger_entry"]
```

```python
# python — opt-in policy, configured once at bootstrap. Pure config is the preferred path.
import forgesight
from forgesight_policy import PolicyEngineInterceptor

forgesight.configure(
    interceptors=[
        PolicyEngineInterceptor.from_config(),   # reads policy.* — driver: native (default)
    ],
)

# An ordinary run is now governed by the declared policy with no further code:
from forgesight import telemetry

with telemetry.agent_run("nightly-summariser", version="2.1.0",
                         metadata={"team": "growth", "environment": "prod"}) as run:
    with run.llm_call(provider="anthropic", model="claude-haiku-4-5") as call:
        ...   # evaluated → Decision(allow, obligations=[amount_usd<=50]); decision emitted.
        ...   # a model.call to "gpt-4.1-preview" here → Decision(deny) → GovernanceSignal.
```

```python
# Catching a deny — identical idiom to feat-020 (the deny IS a GovernanceSignal)
from forgesight_api import GovernanceSignal

try:
    with telemetry.agent_run("etl-agent", metadata={"environment": "prod"}) as run:
        ...
except GovernanceSignal as e:                       # PolicyDenied is a GovernanceSignal (feat-020)
    log.warning("run halted by policy: %s", e)       # run.status == RunStatus.GUARDRAIL
    # The Decision(deny, rule=…, reason=…) already flushed as a forgesight.policy.* event.
```

```yaml
# forgesight.yaml — enable by name (entry-point auto-load, feat-010); no code at all.
interceptors:
  - name: policy
policy:
  driver: native               # "native" (default YAML engine) | "opa" (optional)
  source: file
  path: policy.yaml
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
import { PolicyEngineInterceptor } from '@agentforge/sdk-policy';

configure({ interceptors: [PolicyEngineInterceptor.fromConfig()] });
```

The decision-record helpers and the `Decision` shape operate over the telemetry the SDK
already emits — most teams will query `forgesight.policy.*` events *in their backend* (the
SDK emitted the structured decision; the backend does the reporting). The same `policy.yaml`
is the single document a compliance reviewer reads and a release process pins in git.

### 4.2 Public API / contract

The interceptor implements the **locked** `Interceptor` SPI from feat-001/feat-008 — it is
the *value-add*, namespaced under `forgesight_policy`, and **experimental** within 0.x. The
locked surfaces it rides are `Interceptor.intercept`, the `GovernanceSignal` base + terminal
`RunStatus.GUARDRAIL` (both already in `-api`, feat-020), and `forgesight.usage.cost_usd`
(feat-006). **No new locked SPI is added.** The `PolicyEngine` Protocol and `Decision` /
`PolicyContext` are **package-local** to `forgesight-policy` (a driver-loading detail, not
part of the four-SPI `-api` surface) — exactly the pattern feat-022 used for `RegistrySource`.

```python
# forgesight_policy/model.py — experimental (package-local)
from enum import StrEnum

class Action(StrEnum):
    MODEL_CALL = "model.call"; TOOL_CALL = "tool.call"; SPEND = "spend"

class Effect(StrEnum):
    ALLOW = "allow"; DENY = "deny"

@dataclass(frozen=True, slots=True)
class PolicyContext:
    """What is being decided. Built from the in-flight Record + run metadata (FR-5)."""
    principal: Mapping[str, str]        # who/what — team, repo, environment, data_class, (feat-024 identity)
    action: Action                      # model.call | tool.call | spend
    resource: Mapping[str, str]         # the model / tool / region / amount_usd under decision

@dataclass(frozen=True, slots=True)
class Decision:
    effect: Effect                      # allow | deny
    rule: str | None = None             # id/index of the matched rule (None ⇒ default posture)
    reason: str = ""                    # human-readable, emitted into telemetry
    obligations: tuple[str, ...] = ()   # e.g. ("amount_usd<=50", "require_redaction")
```

```python
# forgesight_policy/engine.py — experimental (package-local Protocol; P5/P10 conformance)
@runtime_checkable
class PolicyEngine(Protocol):
    """Turns a PolicyContext into a Decision. `native` (YAML) ships; `opa` is optional.
    MUST be deterministic and cheap (in-path, P6) and MUST NOT raise into the runtime —
    an engine error yields a Decision per `defaults.on_engine_error` (lean deny / fail-closed)."""
    def evaluate(self, ctx: PolicyContext) -> Decision: ...
```

```python
# forgesight_policy/interceptor.py — experimental
from forgesight_api import Interceptor, Record, GovernanceSignal, RunStatus

class PolicyDenied(GovernanceSignal):
    """A deny Decision halting the run. Subclass of the feat-020 GovernanceSignal →
    RunStatus.GUARDRAIL — NOT a new exception family, NOT a new RunStatus value."""
    def __init__(self, decision: "Decision") -> None: ...
    decision: "Decision"

class PolicyEngineInterceptor(Interceptor):
    def __init__(self, *, engine: PolicyEngine,
                 budget: "BudgetInterceptor | None" = None) -> None: ...  # spend path reuses feat-020
    @classmethod
    def from_config(cls) -> "PolicyEngineInterceptor": ...                # reads policy.* ; resolves driver

    def intercept(self, record: Record) -> Record | None:
        """Build a PolicyContext from the record (action ∈ model.call|tool.call|spend),
        engine.evaluate(ctx) → Decision, EMIT the decision as a forgesight.policy.* event/attrs,
        then: allow ⇒ return record (applying any amount_usd obligation via the BudgetCap path);
        deny ⇒ raise PolicyDenied(decision)  (the sanctioned GovernanceSignal — see §4.3)."""
```

```python
# forgesight_policy/native.py — experimental (the default driver)
class NativePolicyEngine(PolicyEngine):
    """Evaluates the YAML DSL (version/defaults/rules) of §4.1. First matching `when` wins;
    allow-listing posture from `defaults`; numeric caps (amount_usd) become BudgetCap (feat-020)."""
    @classmethod
    def from_file(cls, path: str) -> "NativePolicyEngine": ...
    def evaluate(self, ctx: PolicyContext) -> Decision: ...
```

```typescript
// @agentforge/sdk-policy — experimental (parity)
export interface PolicyEngine { evaluate(ctx: PolicyContext): Decision; }
export class PolicyEngineInterceptor implements Interceptor {
  static fromConfig(): PolicyEngineInterceptor;
  intercept(record: Record): Record | null;
}
```

**Stable (reused, not redefined here):** the `Interceptor` SPI, `GovernanceSignal`,
`RunStatus.GUARDRAIL`, `forgesight.usage.cost_usd`, `BudgetInterceptor`/`BudgetCap`.
**Experimental (this feature):** `PolicyEngine`, `Decision`, `PolicyContext`,
`NativePolicyEngine`, `PolicyEngineInterceptor`, the optional `opa` driver. All
package-local to `forgesight-policy`.

### 4.3 Internal mechanics

The engine is an interceptor that runs on the **start** record of a `model.call` /
`tool.call` / `spend`, *before* the call proceeds (the same hot-path stage feat-008 and
feat-020 use), and **before** export. It evaluates, emits the decision, and on a deny
raises the one sanctioned `GovernanceSignal`:

```
run.llm_call(provider, model)  /  run.tool_call(name)  enters
   │
   ├── build the start Record (model / tool / region / projected amount_usd, metadata FR-5)
   │
   ├── interceptor chain (registration order, feat-008):
   │     PolicyEngineInterceptor.intercept(record):
   │         ctx  = PolicyContext(principal=metadata+identity, action, resource)
   │         dec  = engine.evaluate(ctx)            # native YAML (or opa); deterministic, no I/O
   │         emit forgesight.policy.decision event on the call's span:
   │              effect, rule, reason, obligations            ← DECISION RECORD (always, allow & deny)
   │              (and, if feat-023 audit present, into the audit trail)
   │         if dec.effect == ALLOW:
   │              if "amount_usd<=N" in obligations:           # spend obligation
   │                   record = BudgetInterceptor(BudgetCap(...)).intercept(record)   # reuse feat-020
   │              return record                                # pass
   │         else:  # DENY
   │              raise PolicyDenied(dec)                       # → GovernanceSignal, RunStatus.GUARDRAIL
   │
   └── on PolicyDenied (a GovernanceSignal): set run.status=GUARDRAIL; the run record still
         flushes (telemetry never lost); the call does not proceed; the signal propagates.
```

**The decision record is the headline (P4).** Every evaluation — allow *and* deny —
emits a `forgesight.policy.decision` event with `forgesight.policy.*` attributes on the
call's span. We do **not** invent `gen_ai.*` identifiers (P4 / otel-semconv §4.3) — policy
is an SDK extension, namespaced like cost is (`forgesight.usage.cost_usd`, ADR-0005). So a
deny is observable *with its reason and matched rule*, and an allow-under-obligation is
queryable. When the audit feature (feat-023) is installed, the same `Decision` is written
to the audit trail; absent it, the telemetry event is the record.

**A deny is the sanctioned `GovernanceSignal`, not an export failure (P6 — precise).**
feat-020 established the one case where an interceptor exception is *not* swallowed: a
deliberate governance halt. `PolicyDenied` *is* a `GovernanceSignal`, so it propagates by
design and maps the run to `GUARDRAIL`. **This does not weaken P6 for exporters:** an
*exporter* that raises, hangs, or is misconfigured is still caught, counted, and isolated;
`export()` still returns failure and never raises. The only non-swallowed exception remains
the deliberate control decision — emitting the decision record itself goes through the
normal pipeline and a failure to emit it is swallowed like any export failure (the call is
still governed; the *record* of the decision is best-effort, never a run-breaker).

**Evaluation is in-path but cheap and deterministic (P6).** `native` compiles the YAML
once at `configure()` into an ordered match table; `evaluate` is first-matching-`when` over
an in-memory metadata dict plus glob/allow-list membership and a numeric compare — O(#rules),
no I/O, well inside the < 5 ms p99 hot-path budget (NFR-1). The spend obligation reuses
feat-020's projected-cost path (feat-006 pricing), already on the hot path. The `opa`
driver may add an out-of-process evaluation cost; that is the team's trade-off for adopting
it, and `on_engine_error` (lean fail-closed) bounds the failure mode.

**Driver resolution.** `policy.driver` selects the engine by name from the
`forgesight.policy_engines` entry-point group: `native` ships in this package; `opa` is an
optional, separately-installed driver (P1 — never bundled into core/api). A custom engine is
any package registering under that group and implementing the `PolicyEngine` Protocol.

**Scope boundary (explicit).** ForgeSight enforces at **runtime only** — in the call path at
`model.call` / `tool.call` / `spend`. There is no build-time/CI gate and no
config-resolution-time rejection here: those require resolving a consuming app's *entire*
config to decide it before any call runs, which a runtime telemetry SDK does not own (it
belongs to the app/framework). The `policy.yaml` is a *runtime* document; using the same
file in a CI check is a thing a team may do with their own tooling, not a ForgeSight feature
(see §9).

### 4.4 Module packaging

**Why a sibling package, not an extension of `forgesight-governance`.** This feature *reuses*
`BudgetInterceptor` / `BudgetCap` and the `GovernanceSignal` family from `forgesight-governance`
(and `-api`), so it must depend on them. But it adds two new pluggable surfaces — the
`PolicyEngine` drivers (`native`, optional `opa`) and the decision-record emission — and a
parser for a new DSL. Folding the optional `opa` driver and a DSL parser into
`forgesight-governance` would bloat the package every feat-020 user installs and risk pulling
an OPA dependency near the governance core (P1). So **`forgesight-policy` is a new opt-in
sibling of `forgesight-governance`** (P2): it depends on `-api`, `-core`, and
`-governance`, holds the DSL + engine + interceptor + decision emission, and keeps the
optional `opa` driver in its own extra. The decision is symmetrical with feat-022 shipping
`forgesight-registry` as its own package rather than swelling an existing one.

```bash
pip install forgesight-policy            # native YAML engine + decision records
pip install forgesight-policy[opa]       # optional OPA/Rego driver (not bundled in core, P1)
```

```yaml
# forgesight.yaml — enable by name (entry-point auto-load, feat-010)
interceptors:
  - name: policy
policy:
  driver: native
  source: file
  path: policy.yaml
```

**Entry-point registration** under the existing `forgesight.interceptors` group (the engine
*is* an interceptor — same group feat-008's redaction and feat-020's budget register under)
plus a new `forgesight.policy_engines` group for drivers:

```toml
# forgesight-policy/pyproject.toml
[project.entry-points."forgesight.interceptors"]
policy = "forgesight_policy.interceptor:PolicyEngineInterceptor"

# Policy engine drivers, resolvable by name from policy.driver:
[project.entry-points."forgesight.policy_engines"]
native = "forgesight_policy.native:NativePolicyEngine"

# forgesight-policy-opa/pyproject.toml — the OPTIONAL driver ships its own entry point
[project.entry-points."forgesight.policy_engines"]
opa = "forgesight_policy_opa.engine:OpaPolicyEngine"
```

Chain order matters and is config-controlled, mirroring feat-020: kill-switch (feat-020)
first if present, then this policy engine, then the budget interceptor — so a denied call
never projects cost, and the spend obligation runs only on an allow.

### 4.5 Configuration

```yaml
policy:
  driver: "native"             # "native" (default) | "opa" | "<custom-registered-name>"
  source: "file"               # "file" | "inline"
  path: "policy.yaml"          # required when source == "file"
  inline: null                 # the policy document inline (required when source == "inline")
  actions: ["model.call", "tool.call", "spend"]   # which actions to evaluate (default: all three)
  on_engine_error: "deny"      # "deny" (fail-closed, default) | "allow" (fail-open) — see §8
  emit_decisions: true         # emit forgesight.policy.* decision records (default: true)
  emit_allows: true            # emit on allow too, not just deny (default: true; set false to log denies only)

# The policy DOCUMENT itself (policy.yaml of §4.1):
#   version: <int>
#   defaults: { models: deny-unknown|allow-unknown, tools: deny-unknown|allow-unknown,
#               on_engine_error: deny|allow }
#   rules: [ { when: {<metadata predicate>}, allow|deny|allow_with: {...}, obligations: [...] } ]
```

**Validation rules.** `source: file` requires `path`; `source: inline` requires `inline`.
The policy document's `version` is required (an unknown major version fails fast at
`configure()`). Each rule must set at least one of `allow` / `deny` / `allow_with`; an
`allow_with.amount_usd.max` must be a positive number (it becomes a `BudgetCap`, so the
feat-020 at-least-one-of rule applies). `defaults.models` / `defaults.tools` ∈
`{deny-unknown, allow-unknown}`. `on_engine_error` ∈ `{deny, allow}` (the document-level
`defaults.on_engine_error` overrides the `policy.on_engine_error` block default). `driver`
must resolve in the `forgesight.policy_engines` entry-point group or fail fast with the
group named (feat-010). Unknown keys rejected at `configure()` (architecture §8) — never
mid-run.

**Defaults.** All of `policy.*` is **absent → disabled**; installing the package governs
nothing until a policy is configured (P2 — install necessary, config the enabler). `driver`
defaults `native`; `actions` to all three; `on_engine_error` to `deny` (fail-closed);
`emit_decisions` / `emit_allows` to `true`.

**Env overrides** follow the SDK convention (feat-010): `FORGESIGHT_POLICY_DRIVER`,
`FORGESIGHT_POLICY_SOURCE`, `FORGESIGHT_POLICY_PATH`, `FORGESIGHT_POLICY_ON_ENGINE_ERROR`,
`FORGESIGHT_POLICY_EMIT_DECISIONS`, … with kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

A developer who didn't pick policy at scaffold time adds it later with
`pip install forgesight-policy` + the `interceptors:` / `policy:` YAML + a `policy.yaml` —
no agent-code change, because evaluation rides entirely on the interceptor chain and the
business metadata the SDK already attaches (FR-5). Removing it is `pip uninstall` +
deleting the config; runs keep emitting cost/structure unchanged, just ungoverned by the
declared policy. Adopting `opa` is `pip install forgesight-policy[opa]` + `driver: opa` —
no document or code change beyond the rule dialect the OPA bundle expects.

Upgrade safety (P5): the feature rides the **locked** `Interceptor` SPI, the **locked**
`GovernanceSignal` base + `RunStatus.GUARDRAIL`, and the **locked** `forgesight.usage.cost_usd`
attribute — so a minor bump can add rule keys, obligation kinds, or actions behind defaults
without breaking existing policy. The DSL carries its own `version`; a new document major is
a called-out, opt-in change, and an old document keeps evaluating under its declared version.
The `PolicyEngine` Protocol, `Decision`, and `PolicyContext` are package-local and
experimental within 0.x — signature changes are changelog-called-out; the locked surfaces
beneath them do not move.

## 6. Cross-language parity

Identical across Python / TypeScript: the `policy.yaml` DSL (version / defaults / rules /
when / allow / deny / allow_with / obligations), the allow-listing posture
(`deny-unknown`), the three actions (`model.call` / `tool.call` / `spend`), the
`PolicyContext` → `Decision` shape, first-matching-`when` semantics, the
`forgesight.policy.*` decision-record attributes, the deny-is-`GovernanceSignal` mapping to
`GUARDRAIL`, and `on_engine_error` fail-closed default. Allowed to differ: idiomatic naming
(`fromConfig` vs `from_config`), the `opa` driver's host client, and exception idiom. Python
lands first (0.5); TypeScript follows on the 0.6 parity line — the same cadence feat-020's
governance classes followed.

## 7. Test strategy

- **Unit (DSL/native engine):** `version`/`defaults`/`rules` parsing + schema validation;
  first-matching-`when` precedence; allow-listing posture (`deny-unknown` denies an unlisted
  model/tool; `allow-unknown` passes it); glob model/tool matching (`claude-haiku-*`);
  `deny` set wins inside a matched rule; `allow_with.amount_usd.max` → `BudgetCap`;
  `regions` and `data_class` predicates; `on_engine_error` deny vs allow on a thrown engine.
- **Decision records (headline):** every evaluation emits exactly one
  `forgesight.policy.decision` event with `effect` / `rule` / `reason` / `obligations`
  (snapshot vs the in-memory exporter, feat-011); `emit_allows: false` suppresses the allow
  event but never the deny; a failure to *emit* the decision is swallowed (P6) and does not
  affect enforcement.
- **Enforcement / reuse (feat-020):** a `deny` Decision raises `PolicyDenied` (a
  `GovernanceSignal`), marks the run `GUARDRAIL`, *and* still exports the run record
  (telemetry never lost); an `allow_with: amount_usd<=50` routes through `BudgetInterceptor`
  and trips `BudgetExceeded` at the cap; an allow with no obligation passes untouched.
- **Conformance:** `PolicyEngineInterceptor` runs the feat-011 **`Interceptor` conformance
  suite** unchanged (registration order, isolation, drop-via-`None`) with the deny-raises
  case asserted as the one documented `GovernanceSignal` deviation (the feat-020 pattern);
  a **`PolicyEngine` conformance suite** (P10) every driver — `native` and `opa` — runs:
  determinism (same `PolicyContext` ⇒ same `Decision`), never-raises-into-runtime (errors
  become a `Decision` per `on_engine_error`), and the posture contract.
- **Driver parity:** the same `policy.yaml` semantics evaluated by `native` and by `opa`
  (over an equivalent Rego bundle) produce the same allow/deny on a shared fixture set.
- **Example agent:** a prod agent whose `policy.yaml` allow-lists GA models only; a run that
  attempts a `*-preview` model is denied with the reason visible in the emitted decision
  record — the headline demo and an NFR-1 perf check (evaluation stays within the < 5 ms
  p99 hot-path budget).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| **Fail-closed vs fail-open on engine error** | *Open question; lean fail-closed.* `on_engine_error` defaults to `deny` so a broken/unreachable engine (notably `opa` out-of-process) halts governed calls rather than silently allowing them. `allow` is available for teams that prefer availability over a hard stop — a documented, explicit trade-off. |
| Decision records add hot-path cost / volume | Evaluation is O(#rules), in-memory, no I/O (P6); emission goes through the normal async pipeline (never blocks). `emit_allows: false` lets high-traffic shops record denies only. A failure to emit is swallowed (P6) — enforcement is unaffected. |
| The DSL grows into a general rules engine | Bounded to the three actions (`model.call` / `tool.call` / `spend`) and metadata predicates; anything richer is a custom `PolicyEngine` (e.g. `opa`), not new DSL keywords (§9). |
| Reviewers expect a CI/build-time gate | Out of scope — runtime SDK only (§4.3, §9). The `policy.yaml` is reusable by a team's own CI tooling, but ForgeSight does not resolve an app's full config to gate it pre-run. |
| Overlap with feat-020's `PolicyInterceptor` | feat-020's `PolicyInterceptor` is the code/fragment path; this is the declarative DSL + decision-record path over the *same* mechanism. They compose on one chain; a team picks the surface that fits (raw rules vs a reviewable document). No second governance stack. |
| OPA/Rego pulled near core (P1) | `opa` is an optional, separately-installed driver under its own entry point and extra; `forgesight-policy` core ships only the `native` engine; `-api`/`-core` never see it. |
| `amount_usd` is projected, not actual (the feat-020 caveat) | The spend obligation reuses feat-020's conservative projection (reconciled to actuals on finish); an un-priced model under a `max` cap is treated as "cannot guarantee" and follows the configured posture. |
| Principal beyond metadata | `PolicyContext.principal` is metadata today (FR-5); when identity (feat-024) lands, principal-scoped rules (a verified caller identity, not just a `team` tag) drop into the same `principal` map — additive, no DSL break. |

## 9. Out of scope

- **Build-time / CI policy gates and config-resolution-time rejection.** ForgeSight
  enforces at **runtime only**, in the call path at `model.call` / `tool.call` / `spend`.
  Deciding a policy *before any call runs* requires resolving a consuming app's entire
  config, which a runtime telemetry SDK does not own — it belongs to the app/framework.
  Reusing `policy.yaml` in a team's own CI check is fine; shipping that gate is not this
  feature.
- **A guardrails / PII / content-safety engine.** This feature decides allow/deny over
  model/tool/region/data-class/spend and may *require* a guardrail via an obligation
  (e.g. `require_redaction`), but content guardrails themselves — redaction and
  content-capture gating — are feat-008's job (`ContentCaptureGate`, `PIIRedactionInterceptor`).
  We do not reinvent them; the obligation defers to them.
- **A general-purpose rules engine.** The DSL covers exactly the three actions
  (`model.call` / `tool.call` / `spend`) and metadata predicates. Arbitrary logic, joins,
  or external lookups are not DSL keywords — that is what the pluggable `opa` (or a custom)
  `PolicyEngine` is for.
- **Bundling OPA/Rego (or Cedar) into core.** Any non-`native` engine is an optional,
  separately-installed driver (P1). `-api` / `-core` and `forgesight-policy`'s default
  install carry no such dependency.
- **A policy-authoring UI / approval workflow / policy registry.** The SDK reads a declared
  document and enforces it; visualisation, approval, and distribution of policy live in the
  backend or a separate product (requirements §11 — emit and enforce, don't build
  dashboards).
- **Fleet-wide / cross-process spend obligations.** The `amount_usd` cap reuses feat-020's
  process-local totals; a shared (Redis/DB) counter behind the same `BudgetCap` is the same
  follow-up feat-020 names, not new scope here.
- **A new locked SPI or a new `RunStatus`.** The engine rides the locked `Interceptor` SPI;
  a deny reuses `GovernanceSignal` → `RunStatus.GUARDRAIL`. `PolicyEngine` / `Decision` /
  `PolicyContext` are package-local and experimental.

## 10. References

- [`../requirements.md`](../requirements.md) — FR-10 (interception / policy), FR-9 (cost/spend), §5 (FinOps / governance persona), §11 (emit, don't build dashboards)
- [`../design/design-principles.md`](../design/design-principles.md) — P1 (vendor-neutral; `opa` optional), P2 (opt-in package), P4 (decision telemetry namespaced `forgesight.policy.*`, no `gen_ai.*` squatting), P5 (locked surfaces / experimental package-local), P6 (in-path but cheap; deny is the sanctioned `GovernanceSignal`, exporter failures still swallowed), P10 (`PolicyEngine` conformance suite)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (extensions are namespaced; cost as `forgesight.usage.cost_usd`, ADR-0005 — policy follows the same rule)
- [`../design/architecture.md`](../design/architecture.md) §4 (`Interceptor` SPI, `RunStatus`), §8 (failure modes, fail-fast config validation)
- feat-008 (the `Interceptor` chain — the runtime seam), feat-020 (cost budgets & governance — reuses `BudgetInterceptor`/`BudgetCap`, `PolicyInterceptor`/`PolicyRule`/`PolicyAction`, `KillSwitch`, `GovernanceSignal`), feat-006 (cost/spend — pricing under the `amount_usd` obligation), feat-001 (`Interceptor`, `RunStatus`, `GovernanceSignal`)
- Relates to feat-024 (identity — principal-scoped rules drop into `PolicyContext.principal`) and feat-023 (audit — decisions flow into the audit trail when present)
- Roadmap: features [`README.md`](./README.md) — Phase 3 (governance), extended into the 0.5/0.6 line
