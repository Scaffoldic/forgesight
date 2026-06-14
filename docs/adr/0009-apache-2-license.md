# ADR-0009: Apache 2.0 license

## Metadata

| Field | Value |
|---|---|
| **Number** | 0009 |
| **Title** | Apache 2.0 license |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | licensing, governance |

---

## 1. Context and problem statement

The SDK is an open-source library intended for the broadest possible adoption: by
AgentForge, by third-party agents, by commercial vendors writing integration
packages, and inside proprietary products. The license sets the terms of that
adoption — what downstream users may do, what obligations they take on, and what
patent protection they receive. Because the SDK defines contracts that vendors
will implement and embed, the license choice materially affects who is willing to
build on it.

Which open-source license best maximizes adoption and contribution for a
foundational, vendor-neutral SDK, while providing patent protection and matching
ecosystem norms?

## 2. Decision drivers

- **Maximize adoption.** Commercial and proprietary users must be able to embed
  the SDK without copyleft obligations that would deter them.
- **Patent protection.** A foundational library benefits from an explicit patent
  grant that protects users and contributors.
- **Ecosystem standard.** The OTel ecosystem the SDK builds on is Apache-2.0;
  matching it eases trust, contribution, and code interchange.
- **Consistency with AgentForge.** Aligning the SDK's license with AgentForge
  avoids friction for the combined stack.

## 3. Considered options

1. **Option A — MIT.** Minimal permissive license.
2. **Option B — Apache-2.0.** Permissive with an explicit patent grant.
3. **Option C — a copyleft license (GPL or MPL).** Reciprocal obligations on
   derivative or modified works.

## 4. Decision outcome

**Chosen: Option B — Apache-2.0.**

The SDK is released under Apache License 2.0. It is permissive (no copyleft
obligations, so commercial and proprietary adopters can embed it freely), carries
an explicit patent grant (important for a foundational library that many parties
implement against), and is the de facto standard of the OpenTelemetry ecosystem
the SDK is built on (ADR-0001) — easing trust, contribution, and code
interchange. It also mirrors AgentForge's own license, keeping the combined stack
consistent.

### Positive consequences

- Broadest possible adoption: usable in proprietary and commercial products
  without copyleft friction.
- Explicit patent grant protects users and contributors — valuable for a library
  vendors implement against.
- Matches the OTel ecosystem and AgentForge, easing contribution and code reuse.
- Well-understood by legal teams, lowering the bar to enterprise adoption.

### Negative consequences (trade-offs)

- Permissive terms allow proprietary forks with no obligation to contribute back.
- Slightly more boilerplate than MIT (NOTICE file, patent/attribution clauses).
- No reciprocal copyleft to compel downstream improvements to return upstream.

## 5. Pros and cons of the options

### Option A: MIT

- + Maximally simple and permissive; widely understood.
- − No explicit patent grant — a real gap for a foundational, implemented-against
  library.
- − Less aligned with the OTel ecosystem's Apache-2.0 norm.

### Option B: Apache-2.0 (chosen)

- + Permissive: unrestricted commercial/proprietary use.
- + Explicit patent grant protecting users and contributors.
- + Ecosystem-standard (OTel) and consistent with AgentForge.
- − More boilerplate than MIT; permits non-contributing proprietary forks.

### Option C: a copyleft license (GPL/MPL)

- + Reciprocity can compel downstream improvements to return upstream.
- − Copyleft obligations deter the commercial/proprietary adoption the SDK needs.
- − Out of step with the permissive OTel ecosystem; raises legal-review friction.

## 6. References

- Related ADRs: ADR-0001 (OTel-first; OTel is Apache-2.0), ADR-0002 (the
  packages this license applies to), ADR-0008 (applies across all language
  implementations).
- Design docs: [`../design/design-principles.md`](../design/design-principles.md).
- External: Apache License 2.0 <https://www.apache.org/licenses/LICENSE-2.0>;
  AgentForge license (mirrored here).
