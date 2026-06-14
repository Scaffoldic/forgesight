# ADR-0001: OpenTelemetry-first canonical telemetry model

## Metadata

| Field | Value |
|---|---|
| **Number** | 0001 |
| **Title** | OpenTelemetry-first canonical telemetry model |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, telemetry |

---

## 1. Context and problem statement

The SDK turns agent activity (runs, steps, LLM/tool/MCP calls) into telemetry
and fans it out to many backends — Datadog, Honeycomb, Jaeger, Tempo, SigNoz,
Langfuse, Phoenix, and more. Each of those backends has its own idea of what a
"span", an "attribute", and a "GenAI operation" are. If the SDK lets every
backend define those independently, it owns N parallel, drifting mappings and a
domain model with no single source of truth.

How do we define one canonical telemetry model — span names, attributes,
metrics — that every backend mapping derives from, so the SDK reaches the widest
possible ecosystem without inventing and maintaining a competing standard?

## 2. Decision drivers

- **Vendor-neutral reach for free.** Anything that ingests OTLP should work
  through one exporter with no dedicated package; we want maximum backend
  coverage per unit of maintenance (see [`../design/architecture.md`](../design/architecture.md) §2).
- **Avoid ecosystem fragmentation.** The GenAI observability space already
  suffers from competing attribute vocabularies; adding another one fragments it
  further and strands users on our dialect.
- **Ride the standards body, don't become one.** Defining and evolving a
  semantic-convention spec is a large, ongoing commitment we do not want to own.
- **One deterministic source of truth.** Every non-OTel mapping must derive from
  a single canonical model, not be invented in parallel (P4).

## 3. Considered options

1. **Option A — invent our own conventions.** Define an AgentForge-native
   attribute vocabulary and map each backend from it.
2. **Option B — adopt OpenInference `llm.*` (Arize).** Use the established
   OpenInference vendor conventions as canonical.
3. **Option C — adopt the OTel GenAI semantic conventions.** Make the
   OpenTelemetry GenAI semconv the canonical model; derive all other mappings
   from it.

## 4. Decision outcome

**Chosen: Option C — adopt the OTel GenAI semantic conventions.**

The OTel GenAI semantic conventions become the SDK's canonical model: the domain
model maps deterministically onto OTel traces, metrics, and events, and every
non-OTel backend's mapping is *derived from* that OTel mapping rather than
invented alongside it. This satisfies vendor-neutral reach (any OTLP backend
works with no dedicated package), avoids fragmentation (we speak the emerging
standard, not a dialect), and lets us ride the standards body instead of
becoming one. Where the conventions and our convenience disagree, the
conventions win — with the single explicit exception of cost, which OTel does
not define and which we own as a clearly-namespaced extension (ADR-0005).

### Positive consequences

- The OTLP exporter is the keystone: Datadog, Honeycomb, Jaeger, Tempo, SigNoz,
  New Relic, Phoenix, and Langfuse all work through one package.
- We inherit the spec's design work (span shapes, metric instruments, units,
  buckets) rather than re-deriving it.
- Cross-language parity is easier: both Python and TypeScript emit the same OTel
  identifiers ([`../design/architecture.md`](../design/architecture.md) §10).
- Standards alignment is a marketing and trust signal for adopters.

### Negative consequences (trade-offs)

- The GenAI semconv is entirely at `Development` stability with no tagged
  release, so it can churn. Mitigated by ADR-0004 (pin to a commit, isolate the
  mapping in one module, version it, keep one-minor back-compat on rename).
- We are coupled to the spec's modelling choices even where a bespoke model might
  have fit our domain more snugly.
- Cost has no home in the spec, forcing the one sanctioned extension (ADR-0005).

## 5. Pros and cons of the options

### Option A: invent our own conventions

- + Total control over the model; perfect fit to our domain types.
- − We own and must evolve a full semantic-convention spec.
- − No free OTLP-backend reach; every backend needs a hand-written mapping.
- − Fragments the ecosystem and strands users on our dialect.

### Option B: adopt OpenInference `llm.*` (Arize)

- + Mature, battle-tested in production observability tools.
- − Vendor-origin (Arize); adopting it ties neutrality to one vendor's roadmap.
- − Does not give "any OTLP backend for free" the way the OTel standard does.
- − Still a non-standard dialect relative to where OTel is converging.

### Option C: adopt the OTel GenAI semconv (chosen)

- + Vendor-neutral; broadest backend reach through plain OTLP.
- + Standards-body-maintained; we ride it rather than own it.
- + Single canonical model that all other mappings derive from (P4).
- − All `Development` stability, no release — requires the ADR-0004 mitigations.
- − No cost concept — requires the ADR-0005 extension.

## 6. References

- Related ADRs: ADR-0004 (pin + isolate the mapping), ADR-0005 (cost extension),
  ADR-0006 (the SPIs that exporters implement), ADR-0002 (packaging).
- Design docs: [`../design/design-principles.md`](../design/design-principles.md)
  (P4), [`../design/architecture.md`](../design/architecture.md) §2/§4,
  [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md).
- Prior art: OpenTelemetry GenAI semantic conventions
  (`open-telemetry/semantic-conventions-genai`); OpenInference conventions.
