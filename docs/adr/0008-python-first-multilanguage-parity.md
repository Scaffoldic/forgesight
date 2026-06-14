# ADR-0008: Python-first with multi-language parity roadmap

## Metadata

| Field | Value |
|---|---|
| **Number** | 0008 |
| **Title** | Python-first with multi-language parity roadmap |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | scope, multi-language |

---

## 1. Context and problem statement

Agents are built across languages — Python dominates today, but TypeScript, Java
(Spring AI / Spring Boot), and Go all have meaningful agent ecosystems. The SDK's
value proposition is a *consistent*, vendor-neutral, OTel-first telemetry contract
wherever agents run. But shipping multiple language implementations at once
multiplies cost and risks shipping nothing well. The contracts (domain model,
SPIs, OTel mapping, cost model, config keys, pipeline semantics) are intended to
be language-neutral; only idiom should differ.

How do we sequence the language implementations so we deliver a strong first
release quickly, while guaranteeing that later languages are true parity ports and
not divergent re-imaginings?

## 2. Decision drivers

- **Focus over breadth.** A single excellent first implementation beats two
  half-done ones; the deliverable is described as a "pip package".
- **Largest audience first.** Python is where the most agents — and AgentForge
  itself — live, so Python yields the most value per unit of effort.
- **Parity must be guaranteed, not hoped.** Semantics (contracts, mapping, cost,
  config, pipeline) must be identical across languages; only idiom may differ.
- **A credible roadmap.** Adopters in other languages need a stated trajectory to
  plan around.

## 3. Considered options

1. **Option A — Python-only.** Ship and support Python; treat other languages as
   out of scope.
2. **Option B — Python + TypeScript simultaneously.** Develop both
   implementations in lockstep from day one.
3. **Option C — Python-first, then others.** Ship Python first; define contracts
   language-neutrally; target TypeScript parity by 0.4, then Java (Spring Boot
   starter) and Go.

## 4. Decision outcome

**Chosen: Option C — Python-first with a multi-language parity roadmap.**

Python ships first during 0.x. The contracts — domain model, the four SPIs, the
OTel GenAI semconv mapping (feat-004), the cost model and pricing-table schema,
the `FORGESIGHT_*` config keys and YAML schema, and the pipeline semantics —
are defined **language-neutrally** so that later implementations are parity ports.
TypeScript targets parity by 0.4, with Java (a Spring Boot starter) and Go to
follow; each is tracked per feature via the `Languages` field. What stays
identical across languages: contracts, span names/attributes/metrics, cost model,
config keys, and pipeline semantics. What is allowed to differ: async primitives
(`contextvars`/`asyncio` vs `AsyncLocalStorage`/Promises), packaging (`uv` vs
`pnpm`), the vendor SDK each integration wraps, and idiomatic naming. See
[`../design/architecture.md`](../design/architecture.md) §10.

### Positive consequences

- A focused, high-quality first release for the largest audience and for
  AgentForge itself.
- Language-neutral contracts mean later ports inherit a fixed target, keeping
  cross-language behaviour identical (parity by construction).
- A published roadmap (TS by 0.4, then Java/Go) lets non-Python adopters plan.
- The OTel-first model (ADR-0001) makes parity natural: every language emits the
  same OTel identifiers.

### Negative consequences (trade-offs)

- Non-Python users wait; the SDK is single-language for the early 0.x window.
- Contracts are pinned by the Python implementation's choices, which later
  languages must honour even where another idiom might fit better.
- Maintaining genuine parity across N languages is ongoing effort (shared
  conformance, synchronized re-pins of the semconv mapping per ADR-0004).

## 5. Pros and cons of the options

### Option A: Python-only

- + Maximum focus; no parity burden.
- − Abandons the TS/Java/Go agent ecosystems and the "consistent everywhere"
  value proposition.
- − Contracts risk becoming Python-shaped with no parity discipline.

### Option B: Python + TypeScript simultaneously

- + Two ecosystems served from launch; forces language-neutral contracts early.
- − Doubles cost and risk; likely delays or weakens the first release.
- − Splits limited effort before the contracts have even settled.

### Option C: Python-first, then others (chosen)

- + Strong, focused first release for the biggest audience.
- + Language-neutral contracts make later ports true parity (by construction).
- + Clear roadmap (TS 0.4, then Java/Go) for non-Python adopters.
- − Non-Python users wait; contracts pinned by Python's first choices.

## 6. References

- Related ADRs: ADR-0001 (OTel-first model that makes parity natural), ADR-0002
  (packaging mirrored per language), ADR-0006 (SPIs map to TS `interface` and
  future Java/Go interfaces), ADR-0004 (semconv mapping shared across languages).
- Design docs: [`../design/architecture.md`](../design/architecture.md) §4.3/§10,
  [`../design/design-principles.md`](../design/design-principles.md).
