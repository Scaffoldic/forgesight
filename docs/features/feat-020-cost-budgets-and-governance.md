# feat-020: Cost budgets & governance policies

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-020 |
| **Title** | Cost budgets & governance policies — budget interceptor, policy enforcement, kill-switch |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.3 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core`, `forgesight-governance` |
| **Depends on** | feat-006 (cost model), feat-008 (interceptors) |
| **Blocks** | none |

---

## 1. Why this feature

The SDK already knows what every LLM call costs (feat-006) and already has a hook
that can mutate or veto a record before export (feat-008). The one thing it cannot
yet do is the thing FinOps actually asks for: **stop the spend before it happens.**

Concrete scenarios that hit teams today:

- A retry loop or a runaway ReAct agent calls a frontier model 4,000 times in a
  night. The first anyone hears of it is the monthly invoice — \$11,000 over budget,
  attributable to one misbehaving run that nobody could halt because nothing was
  watching cost *in-flight*.
- A `team=growth` agent is allowed \$200/day; a `team=research` agent is allowed
  \$2,000/day. Today that policy lives in a spreadsheet and a Slack reminder. There
  is no enforcement point — the only lever is "ask people to be careful."
- A prod incident requires cutting off all LLM spend for one repo *right now*, while
  leaving every other agent running. There is no kill-switch; the options are "redeploy"
  or "rotate the provider key and break everything."
- A `environment=prod` agent must never call an experimental, un-priced model, and a
  PII-bearing run must have its content capture forced off regardless of caller config.
  These are *policy* decisions, and right now each agent re-implements them ad hoc.

Cost is the headline telemetry signal (cost-model §1). The signal is worthless for
governance if all it can do is *report* the overspend after it is billed. This
feature turns the cost signal into a **control**.

## 2. Why this belongs in the SDK

- **The SDK is the only component that sees projected cost before the call.** The
  budget check needs `(provider, model, projected token usage) → cost_usd` from the
  same `PricingProvider` (feat-006) that the SDK already resolves cost with. Re-deriving
  projected cost in every agent means re-shipping the pricing table, the model-name
  resolution, and the tiered-pricing logic — exactly the copy-pasted token-to-cost
  calculation requirements §1.1 calls out as the disease.
- **Interceptors are already the sanctioned veto point.** feat-008 defines an
  `Interceptor` SPI that runs in registration order and can return `None` to drop a
  record. A budget that trips a run is the same shape: a hook on the telemetry path
  that can veto. Building it as an interceptor means it composes with redaction and
  content-gating in one chain, with one isolation model (P6), one config surface, and
  one conformance suite (P10). A bespoke per-agent budget guard gets none of that.
- **Governance must be uniform to be trustworthy.** If `team=growth`'s budget is
  enforced one way in agent A and another way in agent B, FinOps cannot reason about
  the fleet. Framework ownership makes "a USD cap per team" mean the *same thing*
  everywhere — the invariant a platform team is buying.
- **Business metadata is already attached at run scope (FR-5, feat-002).** Policy by
  `team` / `repo` / `environment` is just policy keyed on metadata the SDK already
  propagates onto every span. The data is here; only the decision layer is missing.
- **The anti-pattern if we don't:** every team writes a different budget guard, the
  kill-switch is "rotate the key," and cost governance stays a spreadsheet. The SDK
  has the cost signal *and* the veto point; leaving enforcement to agents wastes both.

This is squarely the FinOps / governance persona (requirements §5) and FR-10
(interception / policy), and it is the headline of roadmap **Phase 3 (governance)**.

## 3. How consuming agents/teams benefit

**Before.** An agent author who wants a budget writes their own running-total
accumulator, ships their own copy of the pricing table to project the next call's
cost, wires it into their agent loop, and invents a way to make it kill the run. Call
it ~150 lines of cost-tracking + enforcement glue per agent, and it is wrong the day
a model price changes. The kill-switch does not exist; the platform team's only
fleet-wide lever is the provider key.

**After.**

- **Day 0 — a per-run cap in one config line.** `governance.budgets.per_run.usd: 5.0`.
  The SDK projects the next LLM call's cost from the `PricingProvider` it already has,
  and if the run's accumulated + projected cost would breach \$5 it raises
  `BudgetExceeded`, sets `RunStatus.BUDGET_EXCEEDED`, and the run terminates. Zero
  agent-code change.
- **Day 7 — per-team and per-repo caps, no redeploy.** Caps are keyed on the business
  metadata (`team`, `repo`, `environment`) the SDK already attaches at run scope
  (FR-5). The platform team sets `per_team` / `per_repo` caps centrally; every agent
  carrying that metadata is governed without touching agent code.
- **Day 14 — policy rules.** `deny` an un-priced model in `environment=prod`,
  `redact` content for any run tagged `pii=true`, `allow`-list the models a given team
  may call — declaratively, built on the same `Interceptor` chain as redaction.
- **Incident — a kill-switch that takes effect in seconds.** Flip
  `governance.kill_switch.repo:payments-agent = true` (env var or config reload) and
  every run for that repo trips immediately, while every other agent keeps running.
  No redeploy, no key rotation, no collateral damage.
- **The win is leverage:** the telemetry the SDK *already collects* (cost + business
  metadata) becomes the enforcement substrate. FinOps gets real budgets and a kill
  switch out of data they were already paying to emit.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — opt-in budgets + policy, configured once at bootstrap
import forgesight
from forgesight_governance import BudgetInterceptor, PolicyInterceptor, KillSwitch

forgesight.configure(
    interceptors=[
        BudgetInterceptor.from_config(),     # reads governance.budgets.*
        PolicyInterceptor.from_config(),     # reads governance.policies.*
        KillSwitch.from_config(),            # reads governance.kill_switch.*
    ],
)

# Now an ordinary run is governed with no further code:
from forgesight import telemetry

with telemetry.agent_run("nightly-summariser", version="2.1.0",
                         metadata={"team": "growth", "repo": "summariser",
                                   "environment": "prod"}) as run:
    # The budget is checked *before* each LLM call; an over-budget call raises.
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        ...   # if this projected cost would breach a cap → BudgetExceeded
```

```python
# Catching the trip — the run is marked BUDGET_EXCEEDED, telemetry still flushes
from forgesight_governance import BudgetExceeded

try:
    with telemetry.agent_run("etl-agent", metadata={"team": "research"}) as run:
        ...
except BudgetExceeded as e:
    log.warning("run halted: %s of %s scope %s", e.projected_usd, e.cap_usd, e.scope)
    # run.status is RunStatus.BUDGET_EXCEEDED; the run record exports normally.
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
import { BudgetInterceptor, PolicyInterceptor, KillSwitch } from '@agentforge/sdk-governance';

configure({
  interceptors: [
    BudgetInterceptor.fromConfig(),
    PolicyInterceptor.fromConfig(),
    KillSwitch.fromConfig(),
  ],
});
```

Pure config — no code at all — is the preferred path. With the entry-point auto-load
(feat-010) the three interceptors are resolved by name from `forgesight.yaml`
(see §4.5); the developer installs the package and writes YAML.

### 4.2 Public API / contract

All three classes implement the **locked** `Interceptor` SPI from feat-001/feat-008.
They are the *value-add*, namespaced under `forgesight_governance`, and are
**experimental** (may change within 0.x) — the locked surface they ride on is the
`Interceptor.intercept` signature and `RunStatus.BUDGET_EXCEEDED`, both already in
`-api`.

```python
# forgesight_governance/budget.py — experimental
from forgesight_api import Interceptor, Record, RunStatus, PricingProvider, TokenUsage

class BudgetScope(str, Enum):
    RUN = "run"; TEAM = "team"; REPO = "repo"; ENVIRONMENT = "environment"

@dataclass(frozen=True, slots=True)
class BudgetCap:
    scope: BudgetScope
    key: str | None = None          # e.g. "growth" for scope=team; None = applies to all
    usd: float | None = None        # USD cap; None = no USD cap on this scope
    tokens: int | None = None       # total-token cap; None = no token cap

class BudgetExceeded(Exception):
    """Raised on the hot path when a cap would be breached. Carries the trip context."""
    scope: BudgetScope
    key: str | None
    cap_usd: float | None
    cap_tokens: int | None
    accumulated_usd: float
    projected_usd: float            # accumulated + this call's projected cost

class BudgetInterceptor(Interceptor):
    def __init__(self, *, caps: Sequence[BudgetCap],
                 pricing: PricingProvider | None = None,   # defaults to the configured one
                 on_breach: Literal["raise", "drop", "mark"] = "raise") -> None: ...

    @classmethod
    def from_config(cls) -> "BudgetInterceptor": ...

    def intercept(self, record: Record) -> Record | None:
        """On an LLMCall-start record: project cost via PricingProvider, add to the
        per-scope running totals, and if any cap would be breached enforce on_breach.
        Returns the record unchanged when within budget."""
```

```python
# forgesight_governance/policy.py — experimental
class PolicyAction(str, Enum):
    ALLOW = "allow"; DENY = "deny"; REDACT = "redact"

@dataclass(frozen=True, slots=True)
class PolicyRule:
    match: dict[str, str]           # business-metadata predicate, e.g. {"environment": "prod"}
    action: PolicyAction
    models: tuple[str, ...] = ()    # for allow/deny: the model set the action applies to
    reason: str = ""

class PolicyInterceptor(Interceptor):
    def __init__(self, *, rules: Sequence[PolicyRule]) -> None: ...
    @classmethod
    def from_config(cls) -> "PolicyInterceptor": ...
    def intercept(self, record: Record) -> Record | None:
        """First matching rule wins. DENY → BudgetExceeded-sibling PolicyDenied raised
        (run → GUARDRAIL); REDACT → strips content fields from the record; ALLOW → pass."""
```

```python
# forgesight_governance/kill_switch.py — experimental
class KillSwitch(Interceptor):
    def __init__(self, *, source: "KillSwitchSource") -> None: ...
    @classmethod
    def from_config(cls) -> "KillSwitch": ...
    def intercept(self, record: Record) -> Record | None:
        """If the record's scope key is tripped in the (hot-reloadable) source, raise
        KillSwitchEngaged → RunStatus.BUDGET_EXCEEDED. O(1) set membership; no I/O."""
```

`PolicyDenied` and `KillSwitchEngaged` are siblings of `BudgetExceeded`; all three map
the run to a terminal status (`BUDGET_EXCEEDED` for cost/kill, `GUARDRAIL` for policy
deny) — both already in the locked `RunStatus` enum, so **no new enum value is needed**.

### 4.3 Internal mechanics

The budget check is an interceptor that runs on the **LLMCall-start** record, *before*
the call's record is enqueued for export and (because it runs on the hot path inline,
per feat-008) before the agent proceeds:

```
run.llm_call(provider, model)  enters
   │
   ├── build the start Record (provider, model, projected TokenUsage from request params)
   │
   ├── interceptor chain (registration order, feat-008):
   │     KillSwitch.intercept   → tripped?  raise KillSwitchEngaged
   │     PolicyInterceptor      → deny/redact/allow on business metadata
   │     BudgetInterceptor:
   │         projected = pricing.price(provider, model, projected_usage)
   │         for cap in caps matching this run's scope keys:
   │             if totals[cap] + projected > cap.usd:  enforce on_breach
   │         totals[cap] += projected            # commit on pass
   │
   └── on BudgetExceeded / PolicyDenied / KillSwitchEngaged:
         set run.status; emit RUN_FAILED (reason); enqueue the run record; re-raise.
```

**Projected vs actual cost.** Before the call the SDK only has *requested* params
(`max_tokens`, the prompt's input-token estimate). The interceptor projects on those
(input estimate + `max_tokens` as the worst-case output) so the check is conservative —
it trips *before* an over-budget call rather than after. When the call completes,
feat-006 prices the *actual* usage and the running total is reconciled to actuals on
the finish record, so the next projection starts from the true accumulated spend.

**Per-scope totals are process-local by default.** A `per_run` cap is exact (one run,
one accumulator). `per_team` / `per_repo` / `per_environment` caps aggregate across the
runs *in this process*; a fleet-wide cap across many processes needs a shared counter —
out of scope for 0.3 (see §9), but the `BudgetCap` + total-store split is designed so a
Redis-backed store can drop in behind the same interface later.

**Isolation (P6).** A budget *raise* is a deliberate, caller-visible control-flow
event, not a telemetry failure — so unlike a normal interceptor exception (which feat-008
catches, counts, and skips), `BudgetExceeded` / `PolicyDenied` / `KillSwitchEngaged`
propagate to the caller *by design*. The run record still flushes (telemetry is never
lost); only the agent's *work* is halted. This is the one sanctioned case where an
interceptor's exception is not swallowed, and it is explicit in the `on_breach="raise"`
contract.

**Kill-switch source.** A `KillSwitchSource` is a small pollable interface
(`is_tripped(scope, key) -> bool`) with two shipped implementations: an env-var source
(`FORGESIGHT_KILL_*`, read each call — instant) and a file source (watched / TTL
re-read). It carries no vendor dependency, so it stays in the governance package
without violating P1.

### 4.4 Module packaging

- **`forgesight-core`** gains nothing vendor-specific: it already owns the
  interceptor chain (feat-008), the `PricingProvider` resolution (feat-006), and the
  `RunStatus.BUDGET_EXCEEDED` value (feat-001). The only core-side addition is making
  the projected-cost lookup reachable from an interceptor (a read-only handle to the
  configured `PricingProvider`).
- **`forgesight-governance`** is a new opt-in integration package (P2) holding
  `BudgetInterceptor`, `PolicyInterceptor`, `KillSwitch`, the `BudgetCap` / `PolicyRule`
  config models, and the `KillSwitchSource` implementations. It depends only on `-api`
  and `-core` — **no vendor SDK** (P1).

```bash
pip install forgesight-governance
```

```yaml
# forgesight.yaml — enable by name (entry-point auto-load, feat-010)
interceptors:
  - name: budget
  - name: policy
  - name: kill-switch
```

**Entry-point registration** under the existing `forgesight.interceptors` group
(the same group feat-008's redaction/content-gating interceptors register under):

```toml
# forgesight-governance/pyproject.toml
[project.entry-points."forgesight.interceptors"]
budget = "forgesight_governance.budget:BudgetInterceptor"
policy = "forgesight_governance.policy:PolicyInterceptor"
kill-switch = "forgesight_governance.kill_switch:KillSwitch"
```

Order matters and is config-controlled: kill-switch first (cheapest veto), then policy,
then budget — so a killed or denied run never even projects cost.

### 4.5 Configuration

```yaml
governance:
  budgets:
    # Per-run cap — exact, one accumulator per run.
    per_run:
      usd: 5.0                 # raise BudgetExceeded if a run would exceed $5
      tokens: 2_000_000        # …or 2M total tokens, whichever trips first
    # Per-scope caps keyed on business metadata (FR-5). key → caps.
    per_team:
      growth:   { usd: 200.0 }
      research: { usd: 2000.0 }
    per_repo:
      payments-agent: { usd: 50.0, tokens: 10_000_000 }
    per_environment:
      prod: { usd: 5000.0 }
    on_breach: "raise"         # "raise" | "drop" | "mark"  (default: raise)

  policies:
    rules:
      - match: { environment: "prod" }
        action: "deny"
        models: ["*-experimental", "gpt-*-preview"]   # no unpriced/preview models in prod
        reason: "prod may only call GA models"
      - match: { pii: "true" }
        action: "redact"        # force content capture off for PII-tagged runs
      - match: { team: "growth" }
        action: "allow"
        models: ["claude-haiku-*", "gpt-*-mini"]       # growth: cheap models only

  kill_switch:
    source: "env"              # "env" | "file"
    file_path: null            # required when source == "file"
    poll_seconds: 5            # file source re-read interval
    # env source reads FORGESIGHT_KILL_<SCOPE>_<KEY>=true on each call
```

**Validation rules.** A `BudgetCap` must set at least one of `usd` / `tokens`
(both `null` is a config error). `on_breach` ∈ `{raise, drop, mark}`. A `PolicyRule`
with `action: allow|deny` must set `models`; `action: redact` ignores `models`.
`kill_switch.source: file` requires `file_path`. Unknown keys are rejected at
`configure()` (fail-fast at bootstrap, never mid-run — mirrors architecture §8).

**Defaults.** All of `governance.*` is **absent → disabled**. The package being
installed does nothing until a cap, rule, or kill-switch is configured (P2 — install
is necessary, config is the enabler). `poll_seconds` defaults to 5; `on_breach`
defaults to `raise`.

**Env overrides** follow the SDK convention (feat-010): `FORGESIGHT_GOVERNANCE_*`
and the kill-switch's own `FORGESIGHT_KILL_*` keys, with kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

A developer who didn't pick governance at scaffold time adds it later with
`pip install forgesight-governance` + the `interceptors:` / `governance:` YAML
above — no agent-code change, because budgets and policy ride entirely on the
interceptor chain and the business metadata the SDK already attaches. Removing it is
`pip uninstall` + deleting the YAML block.

Upgrade safety: the package rides the **locked** `Interceptor` SPI and the **locked**
`RunStatus` enum, so a minor bump can add cap scopes or policy actions behind defaults
without breaking existing config. The governance classes themselves are marked
experimental within 0.x; if a signature changes it is called out in the changelog, but
the SPI underneath does not move (P5).

## 6. Cross-language parity

Identical across Python / TypeScript: the `BudgetCap` / `PolicyRule` config schema,
the YAML keys, the scope set (`run`/`team`/`repo`/`environment`), the projected-cost
rule, and the three terminal mappings (`BUDGET_EXCEEDED` / `GUARDRAIL`). Allowed to
differ: idiomatic naming (`fromConfig` vs `from_config`), the kill-switch source
implementations (env/file in both; a runtime's native config-watch may differ), and
exception idiom. Python lands first (0.3); TypeScript follows on the 0.4 parity line.

## 7. Test strategy

- **Unit:** projected-cost math (input estimate + `max_tokens` worst-case) against a
  fake `PricingProvider`; cap-trip boundary (exactly at, just under, just over);
  per-scope total accumulation + reconciliation to actuals on finish; `on_breach`
  variants (`raise` vs `drop` vs `mark`); policy first-match-wins; kill-switch O(1)
  membership.
- **Integration:** a full run that trips `per_run` mid-loop marks the run
  `BUDGET_EXCEEDED` *and* still exports the run record (telemetry never lost); a
  `deny` policy halts with `GUARDRAIL`; a tripped kill-switch halts one repo's runs
  while a sibling repo's run completes.
- **Conformance:** all three classes run the feat-011 **`Interceptor` conformance
  suite** unchanged (registration order, isolation contract, drop-via-`None`) — the
  budget-raise exception is the one documented deviation, asserted explicitly.
- **Example agent:** a "runaway loop" agent that would spend \$50 unbudgeted, halted
  at \$5 by `per_run.usd`, used as the headline demo and a perf check (budget overhead
  stays within NFR-1's < 5 ms hot-path budget).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Projected cost over- or under-estimates the real call | Conservative projection (input estimate + `max_tokens` as worst-case output); reconcile to actuals on finish so drift doesn't compound across calls |
| Cross-process / fleet-wide caps | 0.3 ships process-local totals; the `BudgetCap` + total-store split leaves room for a shared (Redis) store later — explicitly out of scope (§9) |
| Budget raise breaks the "telemetry never breaks the run" invariant (P6) | A budget trip is a *deliberate control*, not a telemetry failure; the run record still flushes, only the agent work halts — documented as the single sanctioned interceptor-raises case |
| Un-priced model can't be budgeted (cost `None`) | Pair with a `deny` policy on unpriced models in governed scopes; a `None` projection is treated as "cannot guarantee under budget" and configurable to deny |
| Kill-switch latency | Env source is read per-call (instant); file source TTL-bounded by `poll_seconds` — a documented trade-off |
| Config typo silently disables a cap | Fail-fast schema validation at `configure()`; at-least-one-of `usd`/`tokens` enforced |

## 9. Out of scope

- **Fleet-wide / cross-process budget aggregation.** 0.3 caps are per-process.
  A shared counter (Redis/DB) behind the same `BudgetCap` interface is a follow-up.
- **Spend forecasting / anomaly detection.** The SDK enforces hard caps; predictive
  "you're trending over" alerting belongs in the backend (requirements §11).
- **A governance dashboard / approval UI.** Emit and enforce only; visualisation and
  approval workflows live in the backend or a separate product (requirements §11).
- **Per-token streaming budgets.** Budgets are checked per LLM call, not per streamed
  token; mid-stream cut-off is not attempted in 0.3.
- **Being the org's policy engine.** Rules are simple metadata-predicate matches, not
  a general policy language (no OPA/Rego). Complex policy → custom interceptor.

## 10. References

- [`../requirements.md`](../requirements.md) — FR-10 (interception / policy), §5 (FinOps persona)
- [`../design/cost-model.md`](../design/cost-model.md) — `PricingProvider`, projected cost (§6 ties budgets to the same SPI)
- [`../design/architecture.md`](../design/architecture.md) §4 (`Interceptor` SPI, `RunStatus`), §8 (failure modes)
- [`../design/design-principles.md`](../design/design-principles.md) — P2, P6, P10
- [`../adr/README.md`](../adr/README.md) — ADR-0005 (cost as namespaced extension), ADR-0006 (Protocol SPIs)
- feat-006 (cost model), feat-008 (interceptors), feat-001 (`RunStatus`, `Interceptor`)
- Roadmap: features [`README.md`](./README.md) — Phase 3 (governance)
