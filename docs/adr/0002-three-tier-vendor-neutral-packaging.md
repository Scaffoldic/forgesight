# ADR-0002: Three-tier, vendor-neutral package model

## Metadata

| Field | Value |
|---|---|
| **Number** | 0002 |
| **Title** | Three-tier, vendor-neutral package model |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, packaging |

---

## 1. Context and problem statement

The SDK will be depended on by AgentForge and by third-party agents, and it
integrates with many backends — each of which drags in its own heavy vendor SDK
(Datadog, Langfuse, ClickHouse, Prometheus client, …). A single mega-package
would pull every vendor SDK as a transitive dependency, bloating installs and
coupling release cycles. At the same time, AgentForge and third-party exporters
need a tiny, stable contract layer they can pin to without inheriting any of that
weight.

How do we structure the pip (and later npm) packages so a consumer installs only
what it uses, the contracts stay stable and dependency-free, and no vendor SDK
ever leaks into the parts everyone depends on?

## 2. Decision drivers

- **Vendor-neutral core (P1).** The depended-on layers must carry zero backend or
  model-provider SDK dependencies — the test of belonging.
- **Plug and play (P2).** A capability is enabled by *installing a package*, never
  by editing core; the unit of distribution is the integration.
- **Stable, pinnable contracts (P5).** The locked surface (domain model + SPIs)
  must live in one leaf package that consumers can pin independently of the
  runtime.
- **Bounded dependency footprint.** Core's runtime deps must stay minimal so the
  SDK is safe to embed in security-conscious deployments.

## 3. Considered options

1. **Option A — monolith + extras.** One `forgesight` package with optional
   extras (`forgesight[langfuse,datadog,…]`) that pull vendor SDKs.
2. **Option B — two-tier.** A contracts/runtime split (`-core` + `-sdk`), with
   integrations as extras or submodules.
3. **Option C — three-tier + integration packages.** `forgesight-api`
   (contracts, zero vendor deps) → `forgesight-core` (runtime; deps = `-api` +
   `opentelemetry-api` only) → `forgesight` (facade); each integration is its
   own package installed to enable.

## 4. Decision outcome

**Chosen: Option C — three-tier + integration packages.**

`forgesight-api` is the leaf: the locked domain model and the four SPIs, with
no I/O and only stdlib + `typing-extensions`. `forgesight-core` is the
runtime — context, span tree, pipeline, metrics, cost, events — depending on
*only* `-api` and the OpenTelemetry **API** (never a vendor SDK).
`forgesight` is the batteries-included facade most users install. Each
backend lives in its own package (`forgesight-otel`, `-langfuse`,
`-prometheus`, …) that depends on `-core` plus its one vendor SDK and is
discovered via entry points. The heart of the model is the **dependency rule**
(locked): `-api` imports nothing inward; `-core` imports only `-api` +
`opentelemetry-api`; integrations import `-core` + their one vendor SDK;
AgentForge depends on `-api` only. This mirrors AgentForge's own three-tier model
(agentforge-py ADR-0003) adapted to telemetry.

### Positive consequences

- A consumer (AgentForge, a custom exporter author) pins to a tiny, dependency-
  free `-api` and is insulated from every vendor SDK.
- New backends ship without touching core (P2); the requirement "add an exporter
  without modifying core" is satisfied structurally.
- Install footprint scales with what you actually use; core stays lean.
- The dependency rule is mechanically enforceable in CI (import-linter), so
  "vendor neutral" is checked, not trusted.

### Negative consequences (trade-offs)

- Three first-party release artefacts (`-api`, `-core`, `-sdk`) plus integration
  packages must be version-coordinated.
- Users must learn that `-api`, `-core`, and `-sdk` are distinct packages.
- Documentation must continually reinforce which tier owns what.

## 5. Pros and cons of the options

### Option A: monolith + extras

- + Simplest single-package install story.
- − Extras still pull vendor SDKs as transitive deps; footprint balloons.
- − No clean boundary: consumers can't pin to contracts alone (violates P1/P5).
- − Couples every backend's release to the core release.

### Option B: two-tier

- + Some separation of contracts from runtime.
- − Without a dedicated leaf, the contract layer accretes runtime concerns.
- − Integrations as extras/submodules still tie vendor SDKs to core releases.

### Option C: three-tier + integration packages (chosen)

- + Crisp boundary: contracts (locked, zero deps) / runtime (lean) / facade.
- + Integrations version independently and are installed to enable (P2).
- + Dependency rule is CI-enforceable; neutrality is guaranteed not hoped.
- − Several artefacts to coordinate; extra teaching cost for the tier split.

## 6. References

- Related ADRs: agentforge-py ADR-0003 (three-tier package model — the prior
  art this mirrors), ADR-0001 (OTel-first model that core encodes), ADR-0006
  (SPIs that live in `-api`), ADR-0008 (multi-language packaging).
- Design docs: [`../design/architecture.md`](../design/architecture.md) §5,
  [`../design/design-principles.md`](../design/design-principles.md) (P1, P2, P5).
