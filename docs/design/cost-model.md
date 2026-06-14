# Design Doc: Cost model & pricing

## Metadata

| Field | Value |
|---|---|
| **Title** | Token → cost: the pricing model |
| **Status** | accepted |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Related features** | feat-006, feat-020 |

---

## 1. Context

Cost is the single most-requested telemetry signal for agents and the one OTel
**deliberately does not standardise** — prices are provider/SKU/region/time-specific.
Every tool in the space (Langfuse, Phoenix, Logfire, Traceloop) fills the gap the same
way: keep the spec's *token* attributes, then compute `cost = tokens × per-model
price` from a self-maintained table, and emit cost as a non-standard attribute. The
SDK owns this (FR-9, P4's sanctioned exception) and makes it pluggable.

## 2. Goals

- Deterministic `(provider, model, TokenUsage) → cost_usd`.
- Accept a provider-supplied cost when present (it wins).
- Pluggable + overridable pricing; unknown models degrade to `null`, never error.
- Support input / output / **cached** / **reasoning** tokens and **tiered**
  (context-dependent) pricing — the real shapes providers bill on.

## 3. Non-goals

- Being the canonical price list for the industry (requirements §11).
- Multi-currency (USD base; conversion is the caller's concern).
- Tokenising text ourselves to *infer* counts — we price counts we're given; missing
  counts → `null` cost (reasoning-token-hidden models can't be inferred anyway).

## 4. Proposal

### 4.1 The SPI

```python
# forgesight_api/spi.py — locked
class PricingProvider(Protocol):
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None: ...
```

Returns USD, or `None` if the model is unknown (caller records tokens, cost `null`,
DEBUG-once). Cost resolution order (highest wins):

1. **Provider-supplied cost** on the `LLMCall` (some APIs return it) — used verbatim.
2. **Caller-registered `PricingProvider`** (custom table / live API).
3. **Default `TablePricingProvider`** (shipped, refreshable).
4. **`None`** — unknown; degrade gracefully.

### 4.2 The pricing table

LiteLLM-style JSON, vendored in `forgesight-core` and refreshable from a pinned
URL (LiteLLM `model_prices_and_context_window.json` / `simonw/llm-prices` /
`pydantic/genai-prices` are compatible sources). Per-model entry:

```json
{
  "anthropic/claude-sonnet-4-5": {
    "provider": "anthropic",
    "input_cost_per_token": 3e-06,
    "output_cost_per_token": 1.5e-05,
    "cache_read_input_token_cost": 3e-07,
    "cache_creation_input_token_cost": 3.75e-06,
    "tiers": [
      { "name": "above-200k", "priority": 1,
        "when": { "input_tokens": { "gt": 200000 } },
        "input_cost_per_token": 6e-06, "output_cost_per_token": 2.25e-05 }
    ]
  }
}
```

Computation: resolve model name (alias + regex-normalise) → select the first matching
**tier** by priority (else base rates) → `sum(token_type_count × rate)` over input,
output, cache_read, cache_creation, reasoning. Reasoning tokens are billed at the
output rate unless a `reasoning_cost_per_token` is given.

### 4.3 Model-name resolution

Providers ship the same model under many strings (dated snapshots, `latest`, Bedrock
ARNs, Azure deployment names). Resolution: exact key → alias map → regex patterns
(priority order, first match) → unknown. Callers can register overrides and aliases
(e.g. map an Azure deployment to a base model). Mirrors Langfuse/LiteLLM.

### 4.4 Emission

Cost is emitted as the extension attribute **`forgesight.usage.cost_usd`** on the LLM
span (never a `gen_ai.*` identifier — ADR-0005) and aggregated into the
`agent_cost_total` metric (feat-005) and `RUN_COMPLETED` events. Per-run cost is the
sum of its LLM-call costs.

### 4.5 Refresh & pinning

The default table is **vendored** (works offline, deterministic in CI) and can be
**refreshed** from a pinned URL on an interval or on demand. Refresh is best-effort
and never blocks startup; a failed refresh keeps the vendored copy. The table carries
an `updated_at`, surfaced so operators know staleness.

## 5. Alternatives considered

| Option | Why not |
|---|---|
| Don't compute cost; emit tokens only | Cost is the headline ask (FR-9); pushing it to every backend re-fragments. |
| Emit cost as `gen_ai.usage.cost` | Spec defines no such attr; risks a future clash. Namespace it (ADR-0005). |
| Tokenise to infer counts when missing | Inaccurate; impossible for hidden reasoning tokens; we price given counts only. |
| Hard-code one pricing table | Stale instantly; not overridable. Pluggable + refreshable instead. |

## 6. Migration / rollout

Lands in feat-006; budgets/governance (feat-020) build on the same `PricingProvider`
(a budget interceptor reads projected cost before an LLM call). The SPI is locked from
v0.1 (design-principles open-Q resolved: cost is core value).

## 7. Risks

| Risk | Mitigation |
|---|---|
| Stale prices | Refreshable table + `updated_at` surfaced + override SPI. |
| Wrong model match | Alias + regex resolution; overridable; unknown → `null` not wrong. |
| Cached/tiered pricing drift | First-class cache + tier fields mirroring provider billing. |

## 8. Open questions

1. Ship the default table inside `-core` or a separate `forgesight-pricing` so it
   updates without a core release? *(leaning: vendored in `-core` + refreshable URL;
   split out only if cadence demands.)*
2. Per-request override of cost (caller passes a known invoice cost)? *(yes —
   resolution order step 1 already covers provider-supplied cost.)*

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-06-14 | `PricingProvider` SPI, locked from v0.1 | Cost is core SDK value |
| 2026-06-14 | LiteLLM-style table, vendored + refreshable, overridable | Proven shape; offline-safe; current |
| 2026-06-14 | Emit `forgesight.usage.cost_usd` | OTel defines no cost attr |

## 10. References

- [`otel-semantic-conventions.md`](./otel-semantic-conventions.md) §4.3
- LiteLLM pricing map, `simonw/llm-prices`, `pydantic/genai-prices`
- feat-006, feat-020
