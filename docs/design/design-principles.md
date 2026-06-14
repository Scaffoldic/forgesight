# Design Doc: ForgeSight design principles

## Metadata

| Field | Value |
|---|---|
| **Title** | ForgeSight — design principles |
| **Status** | accepted |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Supersedes** | none |
| **Superseded by** | none |
| **Related features** | all |

---

## 1. Context

The SDK will be depended on by AgentForge and by third-party agents, and will outlive
the first set of backends it integrates with. It needs a small set of rules that every
feature is checked against, so the project stays coherent as contributors and
integrations multiply. These principles are the constitution; ADRs are the case law.

## 2. Goals

- A contributor can decide "does this belong in core?" without asking.
- A reviewer can reject a PR by pointing at a principle, not an opinion.
- The contracts stay stable enough that AgentForge can pin to them.

## 3. Non-goals

- Prescribing implementation detail (that's per-feature design).
- Covering coding style (that's `.claude/standards/`, when it lands).

## 4. The principles

### P1 — Vendor neutral core

`forgesight-api` and `forgesight-core` depend on **no backend or model-
provider SDK**. Not OpenAI, Anthropic, Langfuse, Datadog, Grafana, ClickHouse. Core
defines contracts and the OTel-shaped model; every vendor lives behind an SPI in its
own package. *Test of belonging:* if a change adds a vendor SDK to core's
dependencies, it's in the wrong package.

### P2 — Plug and play

A capability is enabled by **installing a package**, never by editing core. The unit
of distribution is the integration. `pip install forgesight-langfuse` + one config
line is the whole story. New exporters are added without touching core (success
criterion §10.2 of requirements).

### P3 — Framework agnostic

No framework is privileged **in core**. AgentForge, LangGraph, CrewAI, PydanticAI,
OpenAI Agents, Spring AI, and hand-written agents are all first-class. Framework
*adapters* (feat-019) are separate, opt-in packages — convenience, not coupling.

### P4 — OpenTelemetry first

OTel is the canonical model. The domain model maps deterministically onto OTel
traces / metrics / events via the **GenAI semantic conventions**. Every non-OTel
backend's mapping is *derived from* the OTel mapping, not invented in parallel. When
the conventions and our convenience disagree, the conventions win — with one explicit
exception: **cost**, which OTel does not define, so we own it as a clearly-namespaced
extension (ADR-0005). We do not squat on `gen_ai.*` identifiers the spec hasn't
shipped.

### P5 — Stable contracts

The domain model + four SPIs are **locked surface**. Adding an optional field with a
safe default is a minor bump; removing/renaming a field or changing an SPI signature
is a major bump and needs an ADR. AgentForge and third parties pin to `-api`; we keep
faith with them.

### P6 — Non-blocking & fault tolerant

Telemetry export is asynchronous and isolated. The hot path enqueues and returns;
exporters run on a worker. An exporter that raises, hangs, or is misconfigured is
caught, counted, and isolated — it **never** breaks the agent or sibling exporters.
`export()` returns failure; it does not raise. Queues are bounded; under sustained
backpressure the SDK drops (counted) rather than growing unbounded or blocking.

### P7 — Secure by default

Prompt / completion / tool-argument **content is not captured unless explicitly
opted in** (`capture_content`, mirroring OTel's
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`). Token counts, timing, cost, and
structure are captured by default; *content* is not. PII redaction is a first-class
interceptor, and the content-capture gate is enforced before a record ever reaches an
exporter.

### P8 — Zero magic numbers

Every threshold, timeout, queue size, batch size, and sample rate is a named config
field with a documented default. No literals buried in code.

### P9 — Async-first, no threads for I/O (except the export worker)

The instrumentation API is async-friendly and context-propagating via `contextvars`.
The only background thread is the export worker (mirroring OTel's
`BatchSpanProcessor`), justified because export must survive event-loop stalls and
process exit.

### P10 — Conformance over trust

Every SPI ships a **conformance suite** (feat-011). A "Langfuse exporter" is only an
exporter if it passes the exporter conformance tests. This is how we keep N
integrations honest with one contract.

## 5. Alternatives considered

| Option | Why we didn't pick it |
|---|---|
| One monolithic SDK with optional extras | Pulls vendor SDKs as transitive deps; violates P1/P6 footprint; couples release cycles. |
| Define our own semantic conventions (à la OpenInference `llm.*`) | Fragments the ecosystem; loses "any OTLP backend works for free"; we'd own a spec. We layer on OTel instead (P4). |
| Synchronous export with user-managed threads | Pushes P6 onto every caller; guarantees someone blocks an agent on a slow backend. |
| Capture content by default | Fails P7 / GDPR-HIPAA defaults; the whole ecosystem (OTel, Logfire, Traceloop) defaults off. |

## 6. Migration / rollout

These are foundational; they apply from feat-001. Changing a principle is itself an
ADR + a design-doc revision, and likely a major version.

## 7. Risks

| Risk | Mitigation |
|---|---|
| OTel GenAI conventions are all `Development` and may churn | Isolate the mapping in one module (feat-004), pin to a commit, version the mapping (ADR-0004). |
| "Vendor neutral" tempts a vendor-specific shortcut into core | P1 + the dependency rule are CI-enforced (import-linter); reviewers reject. |
| Async-first raises the bar for contributors | Ship sync shims at the edges; document the model; conformance covers it. |

## 8. Open questions

1. Do we expose a sync-only facade for non-async hosts (Spring Boot bridge, simple
   scripts), or only sync shims over the async core? *(leaning: shims; revisit at
   feat-019.)*
2. Is `PricingProvider` part of the locked surface from v0.1, or experimental until
   the cost model settles? *(leaning: locked; cost is core value — ADR-0005.)*

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-06-14 | Adopt P1–P10 as the SDK constitution | Keep the project coherent as integrations multiply |
| 2026-06-14 | Cost is the single sanctioned exception to "OTel wins" | OTel does not define cost; the SDK must (ADR-0005) |

## 10. References

- [`architecture.md`](./architecture.md)
- [`../requirements.md`](../requirements.md) §2
- [`../adr/README.md`](../adr/README.md)
