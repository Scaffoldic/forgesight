# ADR-0005: Cost as a namespaced extension + pluggable pricing

## Metadata

| Field | Value |
|---|---|
| **Number** | 0005 |
| **Title** | Cost as a namespaced extension + pluggable pricing |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, cost |

---

## 1. Context and problem statement

Cost is the single most-requested telemetry signal for agents — and the one
OpenTelemetry **deliberately does not standardise**, because prices are
provider/SKU/region/time-specific. The GenAI semconv defines token attributes but
no cost attribute and no cost metric. ADR-0001 says "when the conventions and our
convenience disagree, the conventions win", but here the conventions are silent,
not contrary. The SDK must emit cost without squatting on a `gen_ai.*` identifier
the spec has not shipped (which would risk a future clash), and must compute it in
a way that stays current as prices change and as callers bring their own rates.

How do we represent and compute cost as a first-class signal without violating the
OTel-first principle or colliding with a future spec attribute?

## 2. Decision drivers

- **OTel-first with one sanctioned exception (P4).** Cost is the explicit
  exception: OTel defines none, so we own it — but we must not squat on `gen_ai.*`.
- **Cost is core value (FR-9).** It is the headline ask; pushing it to every
  backend re-fragments the very mapping ADR-0001 unifies.
- **Prices drift constantly.** The mechanism must be refreshable and overridable;
  a hard-coded table is stale the day it ships.
- **Graceful degradation.** Unknown models must yield `null` cost (tokens still
  recorded), never an error.

## 3. Considered options

1. **Option A — emit `gen_ai.usage.cost` (squat).** Use a plausible-looking
   `gen_ai.*` attribute the spec hasn't defined.
2. **Option B — emit tokens only.** Record token counts and push cost computation
   onto every backend.
3. **Option C — namespaced extension + pluggable pricing.** Emit the extension
   attribute `forgesight.usage.cost_usd`, computed via a pluggable, refreshable
   `PricingProvider` SPI over a LiteLLM-style table.

## 4. Decision outcome

**Chosen: Option C — namespaced extension + pluggable pricing.**

The SDK owns cost and emits it as the clearly-namespaced extension attribute
**`forgesight.usage.cost_usd`** on the LLM span — never a `gen_ai.*` identifier —
and aggregates it into the `agent_cost_total` metric and `RUN_COMPLETED` events.
Cost is computed through the `PricingProvider` SPI with a resolution order of
provider-supplied cost → caller-registered provider → shipped, refreshable
`TablePricingProvider` → `None`. The default table is LiteLLM-style JSON, vendored
in `-core` (offline-safe, deterministic in CI) and refreshable from a pinned URL,
with alias + regex model-name resolution, cached/reasoning tokens, and tiered
pricing. Unknown models degrade to `null`. The `agentforge.*` namespace makes it
unmistakable that this is our extension, not a spec attribute, so it can never
clash with a future `gen_ai.*` cost field. See
[`../design/cost-model.md`](../design/cost-model.md).

### Positive consequences

- Cost ships as a first-class signal without violating OTel-first (P4's one
  sanctioned exception) and without squatting on `gen_ai.*`.
- Future-proof: if OTel later defines a cost attribute, we adopt it additively
  with no clash, because we never used the namespace.
- Pricing is pluggable, refreshable, and overridable — current prices, caller
  rates, and custom model maps are all supported.
- Unknown models degrade to `null`, never breaking a run (FR-9).

### Negative consequences (trade-offs)

- Backends must learn one non-standard attribute (`forgesight.usage.cost_usd`)
  rather than read a spec-blessed field.
- The SDK takes on ownership of a pricing table that must be kept fresh, with all
  the maintenance and accuracy risk that entails.
- If OTel later standardises cost differently, we carry both our extension and the
  new attribute during a migration window.

## 5. Pros and cons of the options

### Option A: emit `gen_ai.usage.cost` (squat)

- + Looks "standard"; backends reading `gen_ai.*` pick it up automatically.
- − The spec defines no such attribute; a future clash is likely.
- − Directly violates P4's "don't squat on identifiers the spec hasn't shipped".

### Option B: emit tokens only

- + Purest OTel-first stance; the SDK ships no non-standard attribute.
- − Re-fragments the ecosystem: every backend re-derives cost differently.
- − Fails FR-9 — the headline signal is absent from the SDK's own output.

### Option C: namespaced extension + pluggable pricing (chosen)

- + First-class cost, clearly namespaced, future-proof against a spec cost attr.
- + Pluggable, refreshable, overridable pricing; graceful `null` on unknowns.
- − One non-standard attribute for backends to learn; pricing-table upkeep.

## 6. References

- Related ADRs: ADR-0001 (OTel-first; cost is the sanctioned exception), ADR-0004
  (the mapping that emits this extension alongside `gen_ai.*`), ADR-0006
  (`PricingProvider` is one of the four SPIs).
- Design docs: [`../design/cost-model.md`](../design/cost-model.md),
  [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md)
  §4.3, [`../design/design-principles.md`](../design/design-principles.md) (P4).
- Prior art: LiteLLM `model_prices_and_context_window.json`, `simonw/llm-prices`,
  `pydantic/genai-prices`.
