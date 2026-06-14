# ADR-0004: Pin + isolate + version the GenAI semconv mapping

## Metadata

| Field | Value |
|---|---|
| **Number** | 0004 |
| **Title** | Pin + isolate + version the GenAI semconv mapping |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, telemetry, versioning |

---

## 1. Context and problem statement

ADR-0001 makes the OTel GenAI semantic conventions the canonical model. But those
conventions are a moving target: they live in a dedicated repo
(`open-telemetry/semantic-conventions-genai`, moved out of the main
`semantic-conventions` repo), are **entirely at `Development` stability**, have
**no tagged release**, and are actively renaming and migrating identifiers
(e.g. `gen_ai.system` → `gen_ai.provider.name`, and content capture mid-migration
between span attributes and events). If the SDK tracks this spec naively, every
upstream rename can break callers and every backend mapping at once.

How do we depend on an unreleased, churning spec as our canonical model without
exposing callers to that churn, while still being able to advance as the spec
matures?

## 2. Decision drivers

- **Reproducibility.** Without a release to depend on, builds and CI must still be
  deterministic about which revision of the spec they target.
- **Insulate callers (P5).** Consumers depend on our locked domain model, not on
  raw attribute names; spec churn must not be a caller-visible breaking change.
- **Containment.** Spec knowledge must live in one place so a re-pin touches one
  module, not the whole codebase.
- **Auditability.** A backend must be able to tell which revision of the mapping
  produced a given span.

## 3. Considered options

1. **Option A — pin + isolate + version.** Pin to a specific commit of
   `semantic-conventions-genai`, isolate the mapping in one module (feat-004),
   version it via a `semconv_version` resource attribute, and keep one-minor
   back-compat on a rename.
2. **Option B — track `main` continuously.** Always follow the latest commit of
   the spec repo.
3. **Option C — wait for a stable release.** Hold the mapping until upstream cuts
   a tagged, `Stable`-marked GenAI semconv release.

## 4. Decision outcome

**Chosen: Option A — pin + isolate + version.**

The mapping lives **only** in `forgesight-otel` (feat-004) plus a thin set of
attribute-name constants in `forgesight-api`. We **pin to a specific commit**
of `semantic-conventions-genai` (recorded in the feat-004 spec) since there is no
release to pin to. The mapping is **versioned** via a `semconv_version` resource
attribute so any backend can tell which revision produced a span. When upstream
renames an attribute or cuts a release, we re-pin inside feat-004, bump
`semconv_version`, and keep the previous mapping behind a flag for one minor —
re-pinning is a feat-004 change, never a caller-visible one (P5). This lets us
build on an unreleased spec deterministically while shielding consumers, who
depend on our domain model rather than raw OTel identifiers. See
[`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md).

### Positive consequences

- Deterministic, reproducible builds and CI against a fixed spec revision.
- Callers are insulated: a spec rename is an internal re-pin, not a major bump.
- All spec knowledge is in one module, so upgrades are localized and reviewable.
- `semconv_version` makes the emitted revision auditable downstream.
- We can advance with the spec on our own schedule, with one-minor back-compat.

### Negative consequences (trade-offs)

- We lag the absolute bleeding edge of the spec by design; new conventions land
  only when we re-pin.
- Maintaining a one-minor back-compat shim per rename is ongoing work.
- A pinned commit can drift far from `main` if we re-pin infrequently, making the
  eventual catch-up larger.

## 5. Pros and cons of the options

### Option A: pin + isolate + version (chosen)

- + Reproducible builds against a fixed revision despite no release.
- + Callers insulated from churn (P5); re-pin is internal.
- + Single mapping module; auditable via `semconv_version`.
- − Deliberate lag behind `main`; back-compat shims are recurring work.

### Option B: track `main` continuously

- + Always current with the latest spec.
- − Non-reproducible builds; an upstream rename can break callers without warning.
- − Spec churn becomes caller-visible churn (violates P5).

### Option C: wait for a stable release

- + Would give a firm, supported target to depend on.
- − No such release exists; waiting blocks the product indefinitely.
- − Forgoes all the ecosystem reach of ADR-0001 in the meantime.

## 6. References

- Related ADRs: ADR-0001 (OTel-first canonical model this protects), ADR-0005
  (cost extension, the one identifier we add outside `gen_ai.*`), ADR-0002
  (mapping lives in the `-otel` integration + `-api` constants).
- Design docs:
  [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md),
  [`../design/design-principles.md`](../design/design-principles.md) (P4, P5).
- Prior art: `open-telemetry/semantic-conventions-genai`;
  `opentelemetry-util-genai`.
