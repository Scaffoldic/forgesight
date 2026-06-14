# ADR-0003: Async, bounded, fault-isolated export pipeline

## Metadata

| Field | Value |
|---|---|
| **Number** | 0003 |
| **Title** | Async, bounded, fault-isolated export pipeline |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | architecture, reliability |

---

## 1. Context and problem statement

The SDK sits on the agent's hot path: every run, step, and LLM/tool/MCP call
produces a record that must reach one or more backends. Those backends are
network services that can be slow, hang, time out, or be misconfigured. If
export happens inline, a single slow or broken backend stalls or crashes the
agent it is supposed to observe — the worst possible failure mode for a telemetry
SDK. We also have hard non-functional targets: a sub-5 ms p99 hot path (NFR-1),
non-blocking instrumentation (NFR-2), per-exporter fault isolation (NFR-3), and
bounded memory under sustained load (NFR-4).

How do we move records from "produced" to "exported" so the agent is never
blocked or broken by a backend, memory stays bounded under backpressure, and a
faulty exporter cannot take down its siblings?

## 2. Decision drivers

- **Non-blocking & fault tolerant (P6, NFR-1/2/3).** The hot path must enqueue
  and return; a raising, hanging, or misconfigured exporter must be caught,
  counted, and isolated — never breaking the agent or sibling exporters.
- **Bounded memory (NFR-4).** Under sustained backpressure the SDK must drop
  (counted) rather than grow unbounded or block.
- **Graceful shutdown.** Records buffered at exit must be flushable; `force_flush`
  / `shutdown` must drain and close cleanly so nothing is silently lost.
- **Proven shape over novelty.** The mechanism should follow a battle-tested
  design rather than an invented one.

## 3. Considered options

1. **Option A — synchronous inline export.** Each record is exported on the
   calling thread/coroutine before control returns.
2. **Option B — async with an unbounded queue.** A background worker drains an
   unbounded in-memory queue.
3. **Option C — OTel-style bounded async (BatchSpanProcessor shape).** A bounded
   queue (drop-newest + count under backpressure), a single background worker,
   per-exporter `try`/`except` isolation, batched export, and graceful
   flush/shutdown.

## 4. Decision outcome

**Chosen: Option C — OTel-style bounded async pipeline.**

The pipeline adopts the shape of OTel's `BatchSpanProcessor`: each produced
record is built, run through the interceptor chain, and enqueued onto a bounded
queue; a single background worker dequeues in batches and calls each exporter
inside its own `try`/`except`, recording drops and failures as metrics. Under
sustained backpressure the queue drops the newest record and increments
`sdk_records_dropped_total` rather than blocking or growing; `export()` returns
`FAILURE` and never raises (P6); `force_flush` drains the queue and `shutdown`
drains and closes exporters. Following the OTel processor shape gives us a proven
design and aligns the SDK with the standard it already builds on (ADR-0001).
This directly satisfies NFR-1/2/3/4 and P6. See
[`../design/exporter-pipeline.md`](../design/exporter-pipeline.md).

### Positive consequences

- Hot path is O(#interceptors) with no I/O, hitting the sub-5 ms p99 target.
- A broken exporter is isolated and counted; the agent and sibling exporters are
  unaffected (P6, NFR-3).
- Memory is bounded by `max_queue_size`; excess is dropped and counted (NFR-4).
- Mirrors OTel's well-understood processor model, easing maintenance and review.
- Clean flush/shutdown semantics make data loss at exit explicit and bounded.

### Negative consequences (trade-offs)

- Asynchrony introduces eventual-consistency: records are not exported the instant
  they are produced, and a crash can lose the in-flight queue (bounded by design).
- A single background worker is one thread (P9's sanctioned exception), adding a
  small amount of concurrency machinery and lifecycle management.
- Drop-newest-under-backpressure means that, under extreme load, the most recent
  telemetry is the first sacrificed — an explicit, documented trade-off.

## 5. Pros and cons of the options

### Option A: synchronous inline export

- + Simplest; records exported immediately with no worker or queue.
- − Pushes P6 onto every caller; a slow backend blocks the agent (fails NFR-1/2).
- − A raising exporter propagates into the agent (fails NFR-3).
- − No batching; per-record network calls are inefficient.

### Option B: async with an unbounded queue

- + Non-blocking hot path; decouples production from export.
- − Unbounded queue grows without limit under backpressure (fails NFR-4).
- − Still needs the isolation/flush/shutdown machinery — most of the work — but
  with worse memory safety.

### Option C: OTel-style bounded async (chosen)

- + Non-blocking, bounded, fault-isolated, with graceful flush/shutdown.
- + Proven design borrowed from OTel; aligns with ADR-0001.
- + Satisfies NFR-1/2/3/4 and P6 directly.
- − Eventual consistency and a small risk of losing the in-flight queue on crash.

## 6. References

- Related ADRs: ADR-0001 (OTel-first; the pipeline mirrors OTel's processor),
  ADR-0006 (the `TelemetryExporter` SPI the worker calls), ADR-0007 (interceptor
  chain runs before enqueue).
- Design docs: [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md),
  [`../design/architecture.md`](../design/architecture.md) §7/§8/§9,
  [`../design/design-principles.md`](../design/design-principles.md) (P6, P9).
- Prior art: OpenTelemetry `BatchSpanProcessor`; `SpanExportResult`.
