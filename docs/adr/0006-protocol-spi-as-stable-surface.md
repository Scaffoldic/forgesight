# ADR-0006: Protocol-based SPIs as the stable surface

## Metadata

| Field | Value |
|---|---|
| **Number** | 0006 |
| **Title** | Protocol-based SPIs as the stable surface |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, contracts |

---

## 1. Context and problem statement

The SDK's entire extension surface is four service-provider interfaces:
`TelemetryExporter`, `Interceptor`, `EventListener`, and `PricingProvider`. These
are what third parties — and our own integration packages — implement to plug a
new backend, redactor, subscriber, or pricing source into the SDK. The contract
must be small, stable, language-symmetric, and, above all, *low-friction* for an
outside author: writing a Langfuse exporter should not require importing and
subclassing one of our base classes. AgentForge's own framework chose `abc.ABC`
for behavioural contracts (agentforge-py ADR-0007); we must decide whether the
SDK's SPIs follow suit or diverge.

How do we express the four SPIs so third parties can implement them with the least
possible coupling, while keeping the surface enforceable, testable, and stable?

## 2. Decision drivers

- **Lowest-friction third-party extension (P2).** Authoring an exporter should be
  "write a class with these methods", with no required import of an SDK base type.
- **Stable, conformance-tested surface (P5, P10).** The SPIs are locked; every
  implementation must pass a conformance suite — type-checker happiness is not
  enough.
- **Align with OTel's exporter style.** OTel's exporter interfaces are duck-typed;
  matching that idiom lowers the barrier for the OTel-fluent audience.
- **Cross-language parity (ADR-0008).** The contract must translate cleanly to a
  TypeScript `interface` and future Java/Go equivalents.

## 3. Considered options

1. **Option A — ABCs.** Require implementers to subclass an `abc.ABC` base class
   per SPI (the agentforge-py ADR-0007 choice for behavioural contracts).
2. **Option B — structural Protocols.** Define each SPI as a `runtime_checkable`
   `typing.Protocol`; any class with the right methods conforms, no import needed.
3. **Option C — no formal contract.** Pure duck typing with documented method
   names and no declared interface.

## 4. Decision outcome

**Chosen: Option B — structural Protocols (with an optional convenience base
class allowed).**

The four SPIs are `@runtime_checkable` structural `typing.Protocol`s, so a third
party implements them simply by writing a class with the right method signatures —
no inheritance, no import of an SDK base class. The domain model stays as frozen
dataclasses (immutable value types), and exporters can register via decorator or
entry point exactly like shipped integrations. We may *offer* an optional
convenience base class for ergonomics, but it is never required to conform.

The SDK leans Protocol where agentforge-py leaned ABC because the SDK's priority
is **lowest-friction extension by outsiders**: structural typing means a Langfuse
or Datadog exporter author duck-types our interface the same way they already
duck-type OTel's exporter interfaces, with zero dependency on our internals beyond
the value types they receive. Conformance is guaranteed not by inheritance but by
the per-SPI conformance suite (P10) — a "Langfuse exporter" is only an exporter if
it passes the exporter conformance tests. See
[`../design/architecture.md`](../design/architecture.md) §4.

### Positive consequences

- Third parties implement an SPI with no import of an SDK base class — the lowest
  possible coupling (P2).
- Matches OTel's duck-typed exporter idiom, easing adoption by the OTel audience.
- Structural typing still gives `mypy`/`tsc` checking at the call site.
- Maps directly to a TypeScript `interface` and future Java/Go interfaces
  (ADR-0008 parity).
- `@runtime_checkable` plus the conformance suite catches real shape/behaviour
  mismatches, not just compile-time ones.

### Negative consequences (trade-offs)

- No inherited default methods, so each implementer writes the full surface
  (mitigated by the optional convenience base class and the small SPIs).
- `@runtime_checkable` Protocols only check method *presence* at runtime, not
  signatures — conformance tests must carry the behavioural guarantee.
- The SDK and agentforge-py now differ (Protocol vs ABC) on contract style, a
  divergence reviewers and contributors must understand.

## 5. Pros and cons of the options

### Option A: ABCs

- + Inherited defaults; explicit, discoverable base type; `isinstance` works.
- + Consistent with agentforge-py ADR-0007.
- − Requires implementers to import and subclass an SDK base class (friction).
- − Inheritance chains leak implementation and complicate refactors.

### Option B: structural Protocols (chosen)

- + Zero-import, duck-typed implementation — lowest friction (P2).
- + Mirrors OTel's exporter interfaces; clean TS/Java/Go translation.
- + Static checking at call sites; conformance suite carries behaviour (P10).
- − No inherited defaults; runtime check is presence-only.

### Option C: no formal contract

- + Absolute minimum ceremony.
- − No declared surface; conformance impossible to assert from the type system.
- − Type checkers cannot help; breaking changes go unnoticed.

## 6. References

- Related ADRs: agentforge-py ADR-0007 (ABC + Protocol — the choice this
  consciously diverges from for the SDK's SPIs), ADR-0002 (SPIs live in the leaf
  `-api`), ADR-0003 (the exporter SPI the pipeline calls), ADR-0005
  (`PricingProvider` SPI), ADR-0008 (cross-language parity).
- Design docs: [`../design/architecture.md`](../design/architecture.md) §4/§6,
  [`../design/design-principles.md`](../design/design-principles.md) (P2, P5, P10).
- Prior art: OpenTelemetry exporter interfaces; `typing.Protocol` (PEP 544).
