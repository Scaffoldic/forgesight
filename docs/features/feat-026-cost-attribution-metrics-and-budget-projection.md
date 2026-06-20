# feat-026: Cost attribution metrics & pre-call budget projection

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-026 |
| **Title** | Cost attribution metrics & pre-call budget projection — live per-owner/team/agent cost metrics + a projection that denies a call before it spends |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-20 |
| **Target version** | 0.5 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core`, `forgesight-governance` |
| **Depends on** | feat-005 (metrics), feat-006 (cost/pricing), feat-020 (budgets), feat-022 (attribution dimensions) |
| **Blocks** | none |

---

## 1. Why this feature

The SDK already ships three pieces that almost-but-don't-quite close the cost loop:

- feat-006 computes per-call cost and emits `forgesight.usage.cost_usd`.
- feat-005 aggregates that into `forgesight.agent.cost_total` (keyed by provider).
- feat-022 stamps clean ownership dimensions (`team` / `repo` / `owner`) onto every
  run as business metadata (FR-5), and produces **offline** chargeback rollups +
  a catalogue over exported records.
- feat-020 enforces budgets — but only on **completed** LLM records (the shipped
  `BudgetInterceptor` reads `record.llm.cost_usd`, which exists only *after* the call).

Two gaps remain, and both are things only the SDK — sitting at the source, on the
metrics path and the interceptor path — can do well. Concrete scenarios that hit teams
today:

- A platform team wants a **live** cost-by-team dashboard and a page when a team passes
  80% of its monthly budget. Today the cost-by-team number only exists *after* an
  offline group-by over exported records (feat-022's `ChargebackReport`). Between
  exports the dashboard is blind; the 80%-of-budget alert can't fire because there is no
  *live* signal of per-team spend or budget-utilisation — only `cost_total` keyed by
  provider (feat-005), which can't answer "which team."
- A single runaway run calls a frontier model 4,000 times in a night. feat-020 *can*
  halt it — but only on the *completed* record of each call, i.e. **after** that call's
  spend already happened. The cap trips on call N+1 carrying the cost of calls 1..N that
  already billed. The platform team asked for "stop it before it spends"; what shipped
  stops it *after* each spend.
- FinOps wants the budget-utilisation ratio (`spend / cap`) per team as a first-class
  metric they can alert on in their existing stack, not a number they recompute in a
  spreadsheet from an exported CSV once a day.

This feature is the **live + pre-call** delta over what already ships. It is *not* a
re-spec of feat-022's registry or its offline rollups (§9). It adds exactly two things:
(1) emit attributed cost + budget-utilisation as **live metrics** through feat-005, keyed
on the same dimensions feat-022 stamps; and (2) a **pre-call projection** step that
estimates a call's cost *before* it is made and lets a cap deny it before the spend,
closing feat-020's known post-hoc gap.

## 2. Why this belongs in the SDK

- **The live metric can only be emitted where the cost and the dimensions meet.**
  Attributed cost as a *metric* (not an after-the-fact group-by) needs the per-call cost
  (feat-006) and the propagated ownership dimensions (feat-022, FR-5) on the **same**
  record, at the moment the record is produced. The runtime already feeds every record
  to the metrics subsystem (feat-005 §4.3). Adding the attribution dimensions as metric
  *attributes* on a `forgesight.cost.*` counter is a derivation over data the SDK already
  has in hand — no offline job, no second data plane. A side-car that recomputes
  cost-by-team after export is exactly the delayed, per-backend reconstruction feat-022
  exists to avoid; doing it as a live instrument makes the dashboard update in real time.
- **Projection must reuse the one pricing model.** Estimating a call's cost *before* it
  is made is `(provider, model, projected TokenUsage) → cost_usd` through the **same**
  `PricingProvider` (feat-006) the SDK already resolves actual cost with. Re-deriving a
  projection in every agent means re-shipping the pricing table, the model-name
  resolution, and the tiered-pricing logic — the copy-pasted token-to-cost calc
  requirements §1.1 names as the disease. The projection is a read of an SPI the SDK
  already owns.
- **Pre-call enforcement must ride the locked veto point.** feat-020 already established
  that a budget is an interceptor that can veto, and that a budget raise is the one
  sanctioned `GovernanceSignal` (not an export failure). Pre-call projection is the same
  shape moved one step earlier: a check on the **start** record instead of the
  **completed** record. Extending `BudgetInterceptor` with a projection mode keeps one
  config surface, one isolation model (P6), one conformance suite (P10) — a bespoke
  per-agent pre-call guard gets none of that.
- **The anti-pattern if we don't:** cost-by-team stays an offline CSV the dashboard
  can't render live, the 80%-of-budget page can't fire, and budgets keep stopping spend
  *after* each call bills. The SDK has the cost, the dimensions, the metrics path, and
  the veto point already; not closing this last gap wastes all four.

This serves the **FinOps / governance persona** (requirements §5), builds on FR-9
(cost), FR-6 (metrics), FR-5 (business metadata), and FR-10 (interception). It is the
live/pre-call complement to feat-022's declared registry + offline rollups.

## 3. How consuming agents/teams benefit

**Before.** Cost-by-team is an offline group-by over exported records (feat-022); the
live dashboard only shows cost keyed by provider (feat-005), never by team or owner, so
the platform team can't draw a real-time cost-by-team panel and can't alert on
budget-utilisation. Budgets stop a runaway run, but only *after* each call has already
spent — the cap trips carrying the cost of every call up to the trip.

**After.**

- **Day 0 — live attributed cost, zero agent code.** With the registry stamping
  ownership (feat-022) and metrics enabled (feat-005), the SDK emits
  `forgesight.cost.attributed_usd` keyed by `team` / `owner` / `agent.name` as a live
  counter. The platform team's existing backend draws "cost by team this hour" off a
  metric that updates every export interval — no offline job, no CSV.
- **Day 7 — a budget-utilisation gauge to alert on.** When a budget cap is configured
  (feat-020), the SDK also emits `forgesight.cost.budget_utilization` — the ratio of
  accumulated spend to the cap, per scope key. The 80%-of-budget page is now a threshold
  alert on a real-time metric in the team's own stack (we emit, they alert —
  requirements §11).
- **Day 14 — the runaway stops *before* it spends.** Turn on projection
  (`governance.budgets.projection.enabled: true`). The SDK estimates the next call's cost
  from the model + the caller-declared/estimated token counts via the feat-006
  `PricingProvider`, and if accumulated + projected would breach a cap it raises
  `BudgetExceeded` (the existing feat-020 `GovernanceSignal`) **before** the call is
  made. The over-budget call never bills.
- **The win:** the per-run telemetry the team already pays to emit (cost + ownership
  dimensions) becomes a *live* FinOps signal and a *pre-call* control — by flipping
  config, not by writing cost code or running an offline job.

## 4. Feature specifications

### 4.1 User-facing experience

```yaml
# forgesight.yaml — both capabilities are config over what already ships
metrics:
  enabled: true                      # feat-005 metrics on (default)

attribution:
  cost_metrics:
    enabled: true                    # emit forgesight.cost.* (default: false)
    dimensions: ["team", "owner", "agent.name"]   # which stamped dims become metric attrs

governance:
  budgets:
    per_team:
      growth:   { usd: 200.0 }
      research: { usd: 2000.0 }
    projection:
      enabled: true                  # check the cap BEFORE the call, not only after
      output_token_estimate: "max_tokens"   # how to project output tokens (see §4.5)
```

```python
# python — live attributed-cost metrics need NO agent code: the registry (feat-022)
# stamps team/owner, the runtime emits cost (feat-006), feat-005 records the metric.
import forgesight
from forgesight_registry import Registry

forgesight.configure(registry=Registry.from_file("agents.yaml"))   # stamps team/owner

from forgesight import telemetry
with telemetry.agent_run("invoice-parser", version="2.3.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        call.record_usage(input=1200, output=300)
        # on call exit:
        #   forgesight.usage.cost_usd            (feat-006, span attr)
        #   forgesight.agent.cost_total          (feat-005, by provider)
        #   forgesight.cost.attributed_usd       (NEW; by team/owner/agent.name)
        #   forgesight.cost.budget_utilization   (NEW; spend/cap per scope key)
```

```python
# python — pre-call projection: a declared/estimated token count lets the cap deny
# the call BEFORE it bills. The caller supplies the estimate; the SDK does not predict it.
from forgesight_governance import BudgetExceeded

try:
    with telemetry.agent_run("etl-agent", metadata={"team": "research"}) as run:
        with run.llm_call(provider="anthropic", model="claude-opus-4",
                          projected_tokens={"input": 50_000, "max_tokens": 8_000}) as call:
            ...   # projection prices (input + max_tokens) BEFORE the call; if accumulated
                  # + projected would breach research's $2000 cap → BudgetExceeded, call
                  # is never made.
except BudgetExceeded as e:
    log.warning("call denied pre-spend: $%.4f would breach %s cap $%s",
                e.projected_usd, e.scope, e.cap_usd)
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
configure({
  attribution: { costMetrics: { enabled: true, dimensions: ['team', 'owner'] } },
  governance: { budgets: { projection: { enabled: true, outputTokenEstimate: 'max_tokens' } } },
});
```

Pure config is the preferred path: with feat-022 stamping ownership and feat-005 metrics
on, switching `attribution.cost_metrics.enabled` on is the whole story for the live
metric. Projection is one flag on the budget config the team already wrote for feat-020.

### 4.2 Public API / contract

**Live cost-attribution metrics** are emitted through feat-005's `MetricsSubsystem` —
no new public class on the metrics path. Two instruments are added to the
`forgesight.*` product family (namespaced per otel-semantic-conventions §4.3 + the
metric-naming rules; **never** `gen_ai.*` — OTel defines no cost metric, ADR-0005):

| Instrument | Type | Unit | Key attributes |
|---|---|---|---|
| `forgesight.cost.attributed_usd` | Counter | `usd` | the configured `attribution.cost_metrics.dimensions` (e.g. `team`, `owner`, `agent.name`) + `gen_ai.provider.name` |
| `forgesight.cost.budget_utilization` | Gauge (ratio) | `1` | `budget.scope`, `budget.key` (e.g. `team`/`growth`) |

`forgesight.cost.attributed_usd` is the **live** per-owner/team/agent counterpart of
feat-022's offline `ChargebackRow.cost_usd`; `forgesight.agent.cost_total` (feat-005)
stays as-is (provider-keyed). `budget_utilization` is `accumulated_usd / cap_usd` per
scope key, recorded only when a cap is configured for that key (feat-020).

```python
# forgesight_core/metrics/attribution.py — experimental
@dataclass(slots=True)
class AttributionMetricsConfig:                       # stable
    enabled: bool = False                             # off until a team opts in (P2)
    dimensions: tuple[str, ...] = ("team", "owner")   # stamped metadata keys → metric attrs
    # absent dimension on a record → "<unattributed>" bucket (mirrors feat-022 §4.5)

# Added to MetricsSubsystem (feat-005), fed by the same Record stream — agent code never
# touches it. Reads cost from the LOCKED forgesight.usage.cost_usd attr (feat-006) and the
# dimensions from the LOCKED business-metadata mechanism (feat-002/FR-5). No new SPI.
KNOWN_INSTRUMENTS |= {"forgesight.cost.attributed_usd", "forgesight.cost.budget_utilization"}
```

**Pre-call projection** extends the **existing** `BudgetInterceptor` (feat-020) with a
projection mode; it does not add a class. It reuses the locked `PricingProvider`
(feat-006) and raises the existing `BudgetExceeded` / `GovernanceSignal` (feat-020):

```python
# forgesight_governance/budget.py — experimental (extends the shipped BudgetInterceptor)
@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    enabled: bool = False
    output_token_estimate: Literal["max_tokens", "input_ratio", "fixed"] = "max_tokens"
    fixed_output_tokens: int = 0            # used when estimate == "fixed"
    input_ratio: float = 1.0               # used when estimate == "input_ratio"
    on_unpriced: Literal["allow", "deny"] = "allow"   # cost=None → can't guarantee under cap

class BudgetInterceptor:                              # already ships (feat-020)
    def __init__(
        self,
        *,
        caps: Sequence[BudgetCap],
        on_breach: Literal["raise", "drop", "mark"] = "raise",
        pricing: PricingProvider | None = None,       # NEW: defaults to the configured one
        projection: ProjectionConfig | None = None,   # NEW: None ⇒ post-hoc only (today)
    ) -> None: ...

    def intercept(self, record: Record) -> Record | None:
        """Unchanged for completed records (post-hoc reconciliation, feat-020). When
        projection is enabled and the record is an LLM-call *start* (cost not yet known),
        project cost via PricingProvider from the record's projected token counts; if
        accumulated + projected would breach a matching cap, enforce on_breach BEFORE the
        call. BudgetExceeded carries projected_usd as today (feat-020)."""
```

The metrics ride the **locked** `forgesight.usage.cost_usd` attribute (feat-006) and the
**locked** business-metadata mechanism (feat-002, FR-5); projection rides the **locked**
`PricingProvider` SPI (feat-006), the **locked** `Interceptor` shape, and the existing
`GovernanceSignal` / `RunStatus.BUDGET_EXCEEDED` (feat-020) — **no new locked surface.**
`AttributionMetricsConfig` / `ProjectionConfig` are experimental within 0.x.

### 4.3 Internal mechanics

**Live attributed-cost metric — derive, don't double-emit.** The runtime already feeds
every completed `Record` to `MetricsSubsystem.record()` (feat-005 §4.3). Attribution
adds two recordings off that same record — no new instrumentation path:

```
LLM call ends (feat-002 hot path) → Record carries:
   forgesight.usage.cost_usd (feat-006)  +  team/owner/agent.name (feat-022, FR-5)
        │
        ▼  MetricsSubsystem.record(record)            # in-memory, O(1), feat-005
   ├── forgesight.agent.cost_total += cost            # by provider (unchanged, feat-005)
   └── if attribution.cost_metrics.enabled:
          attrs = { d: record.attributes.get(d, "<unattributed>")
                    for d in cost_metrics.dimensions } + provider
          forgesight.cost.attributed_usd += cost  {attrs}      # NEW live counter
          if a cap matches this record's scope key (feat-020 totals):
             forgesight.cost.budget_utilization.set(acc/cap)  {budget.scope, budget.key}
        │
        ▼  MetricReader (push or pull, feat-005 §4.7) → backend draws the live panel
```

An absent dimension on a record buckets under `"<unattributed>"` so cost never silently
vanishes from the metric (mirrors feat-022 §4.5). The dimension set is **bounded** by
config (cost-attribution metric keys are explicitly enumerated, not free metadata) to
keep metric cardinality controlled (feat-005 §8).

**Pre-call projection on the interceptor path.** Today `BudgetInterceptor.intercept`
acts on the **completed** record (`record.llm.cost_usd` exists). Projection adds a branch
on the **start** record, *before* the call proceeds:

```
run.llm_call(provider, model, projected_tokens=…)  enters
   │  build the LLM-call START record (cost_usd is None — call hasn't happened)
   │
   ├── BudgetInterceptor.intercept(start_record):           # projection mode
   │     projected_usage = TokenUsage(input=declared_input,
   │                                  output=estimate(output_token_estimate))   # §4.5
   │     projected = pricing.price(provider, model, projected_usage)            # feat-006
   │     if projected is None:  on_unpriced ("allow" | "deny")
   │     for cap matching this run's scope keys (run/team/repo/environment):
   │         if totals[cap] + projected > cap.usd:  enforce on_breach           # feat-020
   │     # totals are NOT committed here — the start projection is a guard only
   │
   ├── (call proceeds only if not denied) → real provider call
   │
   └── on call END (completed record): feat-020's existing path runs —
         add the ACTUAL cost to totals (reconcile to truth), emit the live metric (above).
```

Projection is a **guard, not a commit**: it estimates to decide whether to *allow* the
call; the per-scope running total is still advanced from **actual** cost on the completed
record (feat-020), so a conservative over-estimate never permanently inflates the
accumulator. The projection is a single in-memory pricing lookup on the interceptor path
— pure CPU, well within the NFR-1 hot-path budget (P6). A projected-budget deny is the
deliberate `GovernanceSignal` (feat-020), **not** an export failure: the run record still
flushes; only the agent's *work* halts.

**Projection accuracy depends on the caller's estimate.** The SDK does **not** predict
prompt sizes. It projects from the caller-declared `projected_tokens` (or a simple
configured heuristic, §4.5) and is honest that the estimate's quality is the caller's:
the value is stopping the spend *before* the call using the best estimate available, vs.
feat-020 stopping it *after* the call bills. A `None` projection (unpriced model) is
treated per `on_unpriced` — `deny` for strict scopes that won't risk an unbounded call.

### 4.4 Module packaging

- **`forgesight-core`** gains the two `forgesight.cost.*` instruments and the
  `AttributionMetricsConfig` inside the existing `metrics/` subsystem (feat-005). It adds
  **no vendor dependency** (P1) — it reads the locked cost attribute and the locked
  business metadata it already has. No new entry point: the instruments are core, like
  the rest of feat-005's inventory.
- **`forgesight-governance`** gains the projection branch on the **existing**
  `BudgetInterceptor` plus `ProjectionConfig`. It still depends only on `-api` and
  `-core` — **no vendor SDK** (P1). No new entry point: the budget interceptor already
  registers under `forgesight.interceptors` (feat-020); projection is a config flag on
  it, not a new interceptor.

```bash
# already installed for feat-020 users; no new package
pip install forgesight-governance
```

```yaml
# enable by config — both rides ship on existing surfaces
attribution:
  cost_metrics: { enabled: true }
governance:
  budgets:
    projection: { enabled: true }
```

No telemetry-path SPI is added: the live metric rides the existing metrics subsystem and
the existing cost/metadata attributes; projection rides the existing interceptor chain
and the existing `PricingProvider`.

### 4.5 Configuration

```yaml
attribution:
  # NB: the registry's stamping config (feat-022) lives under this same `attribution:`
  # block; this feature adds only the `cost_metrics` sub-block.
  cost_metrics:
    enabled: false                   # master switch — off until opted in (P2)
    dimensions: ["team", "owner"]    # stamped metadata keys → metric attributes
    unattributed_label: "<unattributed>"   # bucket for a record missing a dimension

governance:
  budgets:
    # … existing per_run / per_team / per_repo / per_environment caps (feat-020) …
    projection:
      enabled: false                 # off ⇒ budgets stay post-hoc (feat-020 behaviour)
      output_token_estimate: "max_tokens"   # "max_tokens" | "input_ratio" | "fixed"
      fixed_output_tokens: 0         # used when estimate == "fixed"
      input_ratio: 1.0               # output ≈ input_ratio × declared input
      on_unpriced: "allow"           # "allow" | "deny" for cost=None (unpriced model)
```

**Validation rules.** `attribution.cost_metrics.dimensions` must be non-empty when
`enabled` and may name any stamped metadata key (or `agent.name`); unknown keys are *not*
rejected (any record may carry them) but a record missing the key buckets under
`unattributed_label`. `output_token_estimate` ∈ `{max_tokens, input_ratio, fixed}`;
`fixed` requires `fixed_output_tokens > 0`; `input_ratio` requires `input_ratio > 0`.
`on_unpriced` ∈ `{allow, deny}`. Every projection heuristic is a **named, defaulted
config field — no magic numbers** (P8): the projection's worst-case-output rule is the
declared `max_tokens`, the ratio, or a fixed count, never a literal buried in code.
Unknown keys are rejected at `configure()` (fail-fast at bootstrap, architecture §8).

**Defaults.** Both capabilities default **off**: `attribution.cost_metrics.enabled`
false (installing changes nothing until enabled — P2), `governance.budgets.projection.enabled`
false (budgets stay exactly post-hoc as feat-020 ships). `output_token_estimate`
`max_tokens` (conservative); `on_unpriced` `allow`; `unattributed_label` `<unattributed>`.

**Env overrides** (feat-010): `FORGESIGHT_ATTRIBUTION_COST_METRICS_ENABLED`,
`FORGESIGHT_ATTRIBUTION_COST_METRICS_DIMENSIONS`,
`FORGESIGHT_GOVERNANCE_BUDGETS_PROJECTION_ENABLED`,
`FORGESIGHT_GOVERNANCE_BUDGETS_PROJECTION_OUTPUT_TOKEN_ESTIMATE`, … kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

Both capabilities are additive config over packages a feat-020/feat-022 user already has.
Live attributed-cost metrics: set `attribution.cost_metrics.enabled: true` (metrics from
feat-005 + ownership stamping from feat-022 are the only prerequisites) — no agent-code
change, the metric derives from records the runtime already produces. Pre-call
projection: set `governance.budgets.projection.enabled: true` on the budget config you
already wrote — no agent-code change, projection is a branch on the interceptor that's
already in the chain. Turn either off by flipping the flag; runs revert to provider-keyed
`cost_total` (feat-005) and post-hoc budgets (feat-020) with zero residue.

Upgrade safety: the feature rides the **locked** `forgesight.usage.cost_usd` attribute
(feat-006), the **locked** business-metadata mechanism (feat-002), the **locked**
`PricingProvider` SPI (feat-006), and the existing `Interceptor` / `GovernanceSignal` /
`RunStatus.BUDGET_EXCEEDED` (feat-020). New `forgesight.cost.*` instruments are an
additive minor to the feat-005 inventory (P5); new `ProjectionConfig` / projection
behaviour lands behind a default-off flag. The config models are experimental within
0.x — signature changes are changelog-called-out; the surfaces beneath them do not move.

## 6. Cross-language parity

Identical across Python / TypeScript: the two `forgesight.cost.*` instrument names, types,
units, and attribute sets; the `<unattributed>` bucketing rule; the projection token
estimate modes (`max_tokens` / `input_ratio` / `fixed`); the `on_unpriced` rule; and the
"projection guards, actuals commit" semantics. Allowed to differ: idiomatic naming
(`fromConfig` vs `from_config`, `costMetrics` vs `cost_metrics`) and the OTel SDK object
names. Python lands first (0.5); TypeScript follows on the parity line established by
feat-022's 0.4 milestone.

## 7. Test strategy

- **Unit (metrics):** a record carrying `team`/`owner` + cost records
  `forgesight.cost.attributed_usd` on exactly those dimension attributes; a record
  missing a dimension buckets under `<unattributed>`; `budget_utilization` =
  `accumulated / cap` for a configured cap; `cost_metrics.enabled: false` emits neither
  instrument; the dimension set is bounded by config (no free-metadata cardinality blow-up).
- **Unit (projection):** projected cost = `pricing.price(provider, model, projected_usage)`
  for each `output_token_estimate` mode (`max_tokens` worst-case, `input_ratio`, `fixed`);
  cap-trip boundary (just under / at / just over) on the **start** record; an unpriced
  model (`cost=None`) honours `on_unpriced` (`allow` passes, `deny` raises); projection
  guards but does **not** commit — the running total advances only from the actual cost on
  the completed record (reconcile-to-truth).
- **Integration:** a run whose next call would breach a `per_team` cap is denied on the
  **start** record — the provider call is **never made** — and the run still exports
  (telemetry never lost, feat-020 invariant); with projection **off**, the same run
  behaves exactly as feat-020 (post-hoc) — the new branch is inert by default.
- **Live-vs-offline agreement:** the sum of `forgesight.cost.attributed_usd` over a fixture
  equals feat-022's `ChargebackReport.total_usd` on the same dimensions (the live metric
  and the offline rollup agree — they read the same stamped cost).
- **Conformance:** `BudgetInterceptor` with projection on still passes the feat-011
  `Interceptor` conformance suite (registration order, isolation), with the
  projected-budget raise asserted as the documented `GovernanceSignal` deviation (feat-020).
- **Perf:** projection adds one pricing lookup on the interceptor path; overhead stays
  within NFR-1's hot-path budget (P6).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Projection over/under-estimates the real call | The estimate is the caller's (`projected_tokens`) or a configured heuristic; default `max_tokens` is conservative (trips early rather than late); the running total commits from **actual** cost on finish so a guess never compounds (feat-020 reconciliation) |
| Projection accuracy oversold | Spec is explicit: the SDK does **not** predict prompt sizes; projection quality = the caller's estimate quality (§4.3, §9). Value is *pre-spend* vs *post-spend* enforcement, not perfect foresight |
| Unpriced model can't be projected (cost `None`) | `on_unpriced` ∈ `{allow, deny}`; strict scopes set `deny` so an unbounded call is stopped, not waved through |
| Metric cardinality from per-owner/team attrs | Dimension set is **bounded by config** (enumerated keys, not free metadata); `<unattributed>` is a single bucket; mirrors feat-005 §8 cardinality guidance |
| Live metric duplicates feat-022's offline rollup | They are complementary, not duplicate: live metric for real-time dashboards/alerts; offline `ChargebackReport`/catalogue for reports/CI gates. They agree by construction (test in §7) — see §9 |
| `budget_utilization` only meaningful with a cap | Emitted **only** when a cap is configured for that scope key; absent otherwise (no misleading 0/∞) |

## 9. Out of scope

- **The offline chargeback report + agent catalogue.** That is feat-022
  (`ChargebackReport` / `AgentCatalogue` over exported records). This feature is the
  **live metric** and the **pre-call control** — it does *not* re-spec the registry,
  the ownership stamping, or the offline rollups. feat-022 stamps and rolls up offline;
  this emits live + enforces pre-call.
- **A token-count predictor.** Projection uses the caller-declared/estimated token counts
  or a simple configured heuristic (`max_tokens` / ratio / fixed); it does **not** itself
  predict prompt sizes or tokenise text to guess them. Projection accuracy is bounded by
  the estimate the caller provides — stated plainly, not papered over.
- **A billing / ERP integration or multi-currency.** Cost is USD (cost-model §3); pushing
  attributed cost into a billing system, or converting currency, is the caller's concern.
- **A dashboard / alerting UI.** We **emit** `forgesight.cost.*` metrics; the backend draws
  the cost-by-team panel and the user's existing stack fires the 80%-of-budget alert
  (requirements §11 — emit, don't build dashboards or alerting).
- **Fleet-wide / cross-process budget aggregation.** Projection reuses feat-020's
  process-local totals; a shared (Redis/DB) counter behind the same `BudgetCap` interface
  remains the feat-020 follow-up, not this feature.
- **New `gen_ai.*` cost metrics.** OTel defines no cost metric; the new instruments are
  `forgesight.cost.*`, clearly outside `gen_ai.*` (P4, ADR-0005).

## 10. References

- [`../requirements.md`](../requirements.md) — FR-9 (cost), FR-6 (metrics), FR-5 (business metadata), FR-10 (interception), §5 (FinOps persona), §11 (emit, don't build dashboards/alerting)
- [`../design/cost-model.md`](../design/cost-model.md) — `forgesight.usage.cost_usd`, `PricingProvider`, projected cost (§6 ties budgets to the same SPI)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (cost as a namespaced extension), §4.4 (metric instruments / `agentforge.*` vs `gen_ai.*`)
- [`../design/design-principles.md`](../design/design-principles.md) — P1 (vendor-neutral), P4 (OTel-first / namespacing), P5/P10 (locked surfaces + conformance), P6 (non-blocking; governance signal ≠ export failure), P8 (no magic numbers)
- [`../design/architecture.md`](../design/architecture.md) §4 (`Interceptor` SPI, `RunStatus`), §8 (failure modes / config validation)
- feat-005 (metrics subsystem this emits through), feat-006 (cost / `PricingProvider`), feat-020 (budgets — extends `BudgetInterceptor` with projection), feat-022 (attribution dimensions — live counterpart of its offline rollup); relates to feat-024 (identity / principal as an attribution key, planned)
- Roadmap: features [`README.md`](./README.md) — Phase 4 (registry & platform), 0.5 line
