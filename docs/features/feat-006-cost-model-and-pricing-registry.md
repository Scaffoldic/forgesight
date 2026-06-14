# feat-006: Cost model & pricing registry

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-006 |
| **Title** | Cost model & pricing registry (`PricingProvider`, tiered/cache pricing, refreshable table) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` |
| **Depends on** | feat-001, feat-002 |
| **Blocks** | feat-020 |

---

## 1. Why this feature

Cost is the single most-requested telemetry signal for agents — and the one OTel
**deliberately refuses to standardise**, because prices are provider/SKU/region/time
specific. So every team fills the gap the same way: keep the spec's *token* counts, then
multiply by a per-model price from a hand-maintained table, and emit the result under
some made-up attribute. That copy-pasted `tokens × price` calc is wrong within weeks
(prices move, a new model ships, the table goes stale), it doesn't handle the shapes
providers actually bill on (cached tokens, reasoning tokens, tiered context pricing),
and no two teams' "cost per run" mean the same thing.

The concrete pain: a FinOps lead asks "what did the issue-classifier agent cost last
week, per team?" Without a shared cost model, the answer is "every agent computes it
differently, some don't compute it at all, and the Anthropic prompt-cache discount
isn't reflected anywhere." feat-006 ships the deterministic
`(provider, model, TokenUsage) → cost_usd` model once: a locked `PricingProvider`
Protocol, a shipped `TablePricingProvider` over a LiteLLM-style JSON table (input /
output / cache-read / cache-creation / reasoning rates + tiered `when` conditions), a
clear resolution order, robust model-name matching, refreshable-from-pinned-URL with a
vendored offline default, caller overrides, and graceful `cost = None` for unknown
models. Cost is emitted as `forgesight.usage.cost_usd` and rolls up into
`forgesight.agent.cost_total` (feat-005).

## 2. Why this belongs in the SDK (vs each agent rolling its own)

- **What shipping it as the SDK makes possible:** one pricing table, one resolution
  order, one cost number that means the same thing across every agent — so a platform
  team can do real chargeback (`cost per agent`, `cost per team`) across the fleet. An
  agent that inlines its own `tokens × price` gives a number nobody else can trust or
  compare.
- **What the SDK ownership protects:**
  - **The cost attribute boundary.** OTel defines no cost attribute. Left to each
    agent, half would squat on `gen_ai.usage.cost` (a future spec clash) and half would
    invent their own. The SDK emits `forgesight.usage.cost_usd` — a clearly-namespaced
    extension, the *one* sanctioned exception to "OTel wins" (P4, ADR-0005).
  - **Billing-shape correctness.** Cached tokens, prompt-cache *creation* vs *read*,
    reasoning tokens, and tiered (>200k context) pricing are how providers actually
    bill. Encoding them once means every agent gets the discount/surcharge right; a
    hand-rolled calc almost never does.
  - **Freshness + offline determinism.** The table is vendored (so CI is deterministic
    and the SDK works air-gapped) *and* refreshable from a pinned URL with an
    `updated_at` so operators see staleness. No agent should be hard-coding a price list
    that's stale the day it's written.
  - **A locked SPI for governance to build on.** feat-020 (budgets/kill-switch) reads
    *projected* cost through the same `PricingProvider` before a call. That only works
    if the contract is stable and centralised.
- **The anti-pattern if we don't:** N stale price tables, N cost definitions, no
  chargeback, and a cost attribute that collides with a future spec. Exactly the
  fragmentation the SDK exists to end (requirements §1.1).

## 3. How agents/teams consuming the SDK benefit

- **Before:** an agent author copies a price table, writes a `tokens × price` function,
  forgets cache/reasoning/tiers, and watches it go stale.
  **After:** they pass token counts (which the runtime already captures); cost appears as
  `forgesight.usage.cost_usd` automatically — zero cost code.
- **Right cost on day 1, including discounts.** Prompt-cache reads, cache creation, and
  reasoning tokens are priced from first-class fields; tiered context pricing kicks in
  above the threshold — out of the box.
- **Defer the pricing decision.** Use the vendored default table now; later register a
  custom `PricingProvider` (a live pricing API, a negotiated-rate table) as a one-liner
  — no agent rewrite.
- **Provider-supplied cost just works.** When an API returns its own cost, it wins
  (resolution step 1) — no double-pricing, no drift from the invoice.
- **Unknown models never crash.** A brand-new model the table hasn't learned yet records
  tokens and `cost = None` with a once-per-model DEBUG — graceful degrade, not an error
  (FR-9).
- **Fleet-wide chargeback for free.** Per-call cost rolls into
  `forgesight.agent.cost_total` (feat-005), tagged by agent/provider — FinOps gets
  per-team numbers with no agent involvement.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — cost is automatic; the runtime already has the token counts
import forgesight
forgesight.configure()      # ships TablePricingProvider over the vendored table

from forgesight import telemetry

with telemetry.agent_run("issue-classifier") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        ...  # call.usage = TokenUsage(input=1200, output=300, cache_read=900)
    # on call exit: cost_usd = TablePricingProvider.price("anthropic", "claude-sonnet-4-5", usage)
    #   → emitted as span attr  forgesight.usage.cost_usd
    #   → aggregated into        forgesight.agent.cost_total   (feat-005)
```

```python
# python — register a custom pricing provider (resolution step 2 — wins over the table)
from forgesight_api import TokenUsage

class LivePricing:                       # satisfies the PricingProvider Protocol
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        rate = my_pricing_api.lookup(provider, model)
        return None if rate is None else rate.input * usage.input + rate.output * usage.output

forgesight.configure(pricing_provider=LivePricing())

# python — or just override/extend the shipped table (aliases + per-model overrides)
forgesight.configure(pricing_overrides={
    "azure/my-gpt4o-deployment": {"alias": "openai/gpt-4o"},     # map a deployment name
    "anthropic/claude-sonnet-4-5": {"output_cost_per_token": 1.4e-05},  # negotiated rate
})
```

```typescript
// typescript (parity sketch — targets 0.4)
import { configure } from '@agentforge/sdk';
import type { PricingProvider, TokenUsage } from '@agentforge/sdk-api';

const live: PricingProvider = {
  price(provider, model, usage: TokenUsage) { /* … */ return null; },
};
configure({ pricingProvider: live });
```

### 4.2 Public API / contract

```python
# forgesight_api/spi.py — LOCKED (P5; ADR-0005, ADR-0006)
@runtime_checkable
class PricingProvider(Protocol):
    """Resolve cost. Returns None for unknown models (degrade gracefully)."""
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None: ...

# forgesight_core/cost/table.py
class TablePricingProvider:                            # stable — the shipped default
    """LiteLLM-style table over (provider, model) → rates. Vendored + refreshable."""
    def __init__(
        self,
        table: PricingTable | None = None,             # None ⇒ vendored default
        *,
        overrides: dict[str, dict] | None = None,      # per-model overrides + aliases
    ) -> None: ...
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None: ...
    @classmethod
    def from_vendored(cls) -> "TablePricingProvider": ...
    @classmethod
    def from_url(cls, url: str, *, timeout_s: float = 5.0) -> "TablePricingProvider": ...
    def refresh(self) -> bool: ...                      # best-effort; keeps old copy on fail
    @property
    def updated_at(self) -> datetime | None: ...        # staleness surfaced to operators

# forgesight_core/cost/resolver.py
class CostResolver:                                    # experimental — internals may move
    """Applies the resolution order (§4.3) per LLMCall; called by the runtime."""
    def resolve(self, call: LLMCall, provider: PricingProvider | None) -> float | None: ...
```

**Pricing-table schema** (LiteLLM-style JSON; vendored in `-core`, refreshable):

```json
{
  "updated_at": "2026-06-14T00:00:00Z",
  "models": {
    "anthropic/claude-sonnet-4-5": {
      "provider": "anthropic",
      "input_cost_per_token": 3e-06,
      "output_cost_per_token": 1.5e-05,
      "cache_read_input_token_cost": 3e-07,
      "cache_creation_input_token_cost": 3.75e-06,
      "reasoning_cost_per_token": null,
      "tiers": [
        { "name": "above-200k", "priority": 1,
          "when": { "input_tokens": { "gt": 200000 } },
          "input_cost_per_token": 6e-06, "output_cost_per_token": 2.25e-05 }
      ]
    }
  },
  "aliases": { "claude-sonnet-4-5-latest": "anthropic/claude-sonnet-4-5" }
}
```

**Cost resolution order (highest wins — cost-model §4.1):**

1. **Provider-supplied cost** on the `LLMCall` (some APIs return it) — used verbatim.
2. **Caller-registered `PricingProvider`** (custom table / live API).
3. **Default `TablePricingProvider`** (shipped, refreshable).
4. **`None`** — unknown model; degrade gracefully (record tokens, cost null,
   DEBUG-once).

**Computation:** resolve model name → select the first matching **tier** by priority
(else base rates) → `sum(token_type_count × rate)` over input, output, cache_read,
cache_creation, reasoning. Reasoning tokens bill at the output rate unless
`reasoning_cost_per_token` is given. Input tokens are priced incl. cached per the spec's
token semantics, with cache-read/creation deltas applied from their own fields.

### 4.3 Internal mechanics

**Model-name resolution** (cost-model §4.3) — providers ship one model under many
strings (dated snapshots, `latest`, Bedrock ARNs, Azure deployment names):

```
exact key  →  alias map  →  regex patterns (priority, first match)  →  unknown (None)
```

Callers register overrides + aliases (e.g. map an Azure deployment to a base model).
Mirrors Langfuse / LiteLLM. Lookup is O(1) on the exact/alias path plus one
regex-normalise on miss; the table is loaded once (architecture §9).

**Refresh & pinning** (cost-model §4.5):

```
configure()
   └── TablePricingProvider.from_vendored()      # offline-safe, deterministic in CI
          └── if refresh configured (URL + interval):
                 best-effort refresh()            # never blocks startup
                    ├── success → swap table, update updated_at
                    └── failure → keep vendored copy, WARN once
```

`updated_at` is surfaced so operators know staleness. Refresh is best-effort and never
blocks the agent (P6); a failed refresh keeps the last-known-good table.

**Emission & rollup** (cost-model §4.4):

```
LLMCall ends → CostResolver.resolve(call, provider)
   → cost_usd (or None)
   → span attr  forgesight.usage.cost_usd        (extension; never gen_ai.* — ADR-0005)
   → metric     forgesight.agent.cost_total += cost   (feat-005)
   → event      RUN_COMPLETED carries per-run cost = Σ its LLM-call costs   (feat-007)
```

Per-run cost is the sum of its LLM-call costs. The resolver runs on the worker/finish
path, not the hot path; pricing is pure CPU (no I/O except the optional background
refresh — P9).

### 4.4 Module packaging

- Lives in **`forgesight-core`** (always installed) — cost is core SDK value, and
  the vendored table makes it offline-deterministic. The `PricingProvider` Protocol
  itself is in `forgesight-api` (the locked SPI). Deps: stdlib + the small JSON
  table only — **no** vendor SDK (P1).
- No separate install. (Open question, cost-model §8: split the table into
  `forgesight-pricing` if refresh cadence ever outpaces core releases; leaning
  vendored-in-core + refreshable URL for now.)

  ```yaml
  # forgesight.yaml
  cost:
    pricing_source_url: "https://raw.githubusercontent.com/BerriAI/litellm/<pinned>/model_prices_and_context_window.json"
    refresh_interval_s: 86400          # daily; 0 disables refresh (vendored only)
    overrides:
      azure/my-gpt4o-deployment: { alias: "openai/gpt-4o" }
      anthropic/claude-sonnet-4-5: { output_cost_per_token: 1.4e-05 }
  ```

- **Entry point:** a custom `PricingProvider` registers via
  `@forgesight.register("pricing", "my-provider")` or a `pyproject.toml` entry point
  under `forgesight.pricing`, resolvable by name from config (architecture §6).

### 4.5 Configuration

| Key (YAML under `cost:`) | Env | Default | Validation |
|---|---|---|---|
| `pricing_source_url` | `FORGESIGHT_PRICING_SOURCE_URL` | pinned LiteLLM URL | URL; refresh source |
| `refresh_interval_s` | `FORGESIGHT_PRICING_REFRESH_INTERVAL_S` | `86400` | int ≥ 0; `0` ⇒ vendored only, no refresh |
| `overrides` | `FORGESIGHT_PRICING_OVERRIDES` | `{}` | map of model → `{alias}` or rate fields; merged over the table |

Constructor `pricing_provider=` / `pricing_overrides=` override env, which override YAML
(last-wins; feat-010). A registered `PricingProvider` (resolution step 2) supersedes the
table entirely. Provider-supplied cost (step 1) supersedes everything. Every knob is
named + defaulted (P8).

## 5. Plug-and-play & upgrade story

In `forgesight-core` — always installed; no scaffold-time choice. Swap the cost
source later by registering a `PricingProvider` (config or entry point) — no agent code
change (P2). The vendored table refreshes from the pinned URL without a release; a stale
or unreachable source falls back to the last-known-good copy. Upgrade safety: the
`PricingProvider` Protocol is **locked from v0.1** (cost is core value — ADR-0005,
design-principles open-Q resolved); the table *schema* may gain optional fields
(minor), but `price()`'s signature is frozen, so feat-020's budget interceptor and any
custom provider survive minor bumps (P5).

## 6. Cross-language parity

Identical across Python / TypeScript: the `PricingProvider` Protocol/interface, the
resolution order, the table schema (incl. cache/reasoning/tiered fields + `when`
conditions), the model-name resolution rules, the `forgesight.usage.cost_usd` emission,
and the `None`-for-unknown degrade. Allowed to differ: HTTP-refresh client, idiomatic
naming (`contextvars` vs `AsyncLocalStorage` is irrelevant here — pricing is pure). USD
base only; multi-currency is the caller's concern (cost-model §3). Python first (0.1);
TS by 0.4.

## 7. Test strategy

- **Unit:** known model + token counts → exact cost (incl. cache_read, cache_creation,
  reasoning); tiered `when` selects the right rate above/below threshold; reasoning bills
  at output rate when `reasoning_cost_per_token` absent.
- **Resolution order:** provider-supplied cost wins over a registered provider, which
  wins over the table, which wins over `None`.
- **Model-name resolution:** exact → alias → regex → unknown; an Azure deployment alias
  resolves to the base model; unknown → `None` + DEBUG-once (no error — FR-9).
- **Refresh:** a reachable URL swaps the table + updates `updated_at`; an unreachable URL
  keeps the vendored copy and WARNs; refresh never blocks startup (P6).
- **Overrides:** caller overrides + aliases merge over the shipped table.
- **Emission/rollup:** cost lands on `forgesight.usage.cost_usd` (never `gen_ai.*`) and
  sums into `forgesight.agent.cost_total` and the `RUN_COMPLETED` event.
- **Conformance:** runs the feat-011 `PricingProvider` conformance suite.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Stale prices | Refreshable table + `updated_at` surfaced + override SPI (cost-model §7). |
| Wrong model match | Alias + regex resolution; overridable; unknown → `None`, not a wrong number. |
| Cached/tiered pricing drift from provider billing | First-class cache + tier fields mirroring how providers bill. |
| Cost attribute clashes with a future `gen_ai.usage.cost` | Namespaced `forgesight.usage.cost_usd`; never squat `gen_ai.*` (ADR-0005). |
| Ship the table in `-core` or a separate `forgesight-pricing`? | Leaning vendored-in-core + refreshable URL; split only if cadence demands (cost-model §8). |
| Missing token counts (hidden reasoning tokens) | Price only counts we're given; missing → `None`. We never tokenise to infer (cost-model §3). |

## 9. Out of scope

- **Being the canonical industry price list** — refreshable + overridable table, not the
  source of truth (requirements §11).
- **Multi-currency** — USD base; conversion is the caller's concern (cost-model §3).
- **Inferring token counts by tokenising text** — we price given counts; impossible for
  hidden reasoning tokens anyway.
- **Budgets / kill-switch / policy enforcement** — feat-020 builds *on* this
  `PricingProvider` (reads projected cost before a call); not in this feature.
- **Cost as a `gen_ai.*` attribute or metric** — OTel defines none; we namespace it
  (P4, ADR-0005).

## 10. References

- [`../design/cost-model.md`](../design/cost-model.md) — the design this feature implements
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 — where `forgesight.usage.cost_usd` is emitted
- [`../design/architecture.md`](../design/architecture.md) §4 (`PricingProvider` SPI), §9 (cost-lookup perf)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P4, P5, P8
- [`../adr/0005-cost-as-namespaced-extension.md`](../adr/0005-cost-as-namespaced-extension.md), [`../adr/0006-protocol-spi-as-stable-surface.md`](../adr/0006-protocol-spi-as-stable-surface.md)
- feat-001 (`PricingProvider` SPI + `TokenUsage`), feat-002 (runtime — token source), feat-005 (`agent.cost_total` rollup), feat-020 (budgets — builds on this)
- LiteLLM pricing map, `simonw/llm-prices`, `pydantic/genai-prices`
