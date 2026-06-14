# feat-003: Async export pipeline

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-003 |
| **Title** | Async export pipeline (bounded queue, batching, fault isolation, flush/shutdown) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` (import root `forgesight_core`) |
| **Depends on** | feat-001 |
| **Blocks** | feat-004, feat-012, feat-013, feat-014 |

---

## 1. Why this feature

feat-002 builds an immutable `Record` and needs somewhere to put it that is
*instant and safe*. feat-003 is that somewhere: the machinery between "a record
was produced" and "an exporter was called." It is the feature that makes the
SDK's headline promises true — **telemetry never blocks the agent, never fails
the agent, never grows memory unbounded, and fans out to many backends at once.**

The pain it removes is the failure every hand-rolled observability layer
eventually hits:

- **A slow backend stalls the agent.** Someone calls `langfuse.flush()` or an
  OTLP exporter inline on the request path; the backend is having a bad day; now
  the agent's p99 latency is the backend's p99 latency. The user waits on
  telemetry.
- **A backend outage crashes the agent.** An exporter raises (DNS failure, 503,
  bad auth) and the exception propagates into the agent loop. Observability — the
  thing that's supposed to help during an incident — *causes* the incident.
- **A backpressure spike OOMs the process.** Under a burst the unbounded buffer
  grows until the process dies. The cure (drop some telemetry) is worse only if
  it's silent — and hand-rolled buffers drop silently.
- **Fanning out to N backends means N hand-written threads.** OTLP *and*
  Langfuse *and* a custom sink, each with its own ad-hoc batching and error
  handling, each able to take down the others.

This is a solved shape — OpenTelemetry's own `BatchSpanProcessor` /
`SpanExporter` model — and feat-003 adopts it deliberately rather than inventing
one ([exporter-pipeline](../design/exporter-pipeline.md),
[ADR-0003](../adr/0003-async-fault-isolated-export-pipeline.md)).

## 2. Why this belongs in the SDK core (vs each agent/team rolling its own)

- **NFR-1/2/3/4 are guarantees the SDK makes on behalf of every agent.** "Hot
  path < 5 ms, non-blocking, fault-tolerant, bounded memory at 100k+ runs/day"
  are not things an agent author should have to re-derive correctly. They are
  *invariants* the pipeline enforces once, centrally, so every agent inherits
  them. An agent that rolls its own will get the easy 80% right and the
  fault-isolation/backpressure 20% wrong — exactly the parts that matter during
  an outage.
- **Fault isolation is the difference between "observability helps" and
  "observability hurts" (P6).** The rule "one exporter raising/hanging/
  mis-configured affects nothing else and never the agent" is subtle: it needs a
  per-exporter try/except, a non-raising `export()` contract, a hard timeout, and
  a counter. Get any piece wrong and a single bad backend takes down the run.
  This must be written once and proven by a conformance test (P10).
- **Multi-backend fan-out (FR-11) only works if the fan-out is owned centrally.**
  The whole point of the SDK is "emit to OTLP *and* Langfuse *and* ClickHouse
  from the same run, each isolated." That single record → many exporters fan-out
  is the pipeline's job; if each agent wires its own, the isolation guarantee
  evaporates at the seams.
- **Bounded-with-counted-drops is a safety property, not a feature toggle**
  (NFR-4). "Drop the newest record and increment `sdk_records_dropped_total`
  under sustained backpressure" keeps memory bounded *and* keeps the loss
  observable. A per-agent buffer either grows unbounded (OOM) or drops silently
  (mystery data loss). Neither is acceptable; the central pipeline does the
  correct third thing.

**Anti-pattern if left to each agent:** an inline `exporter.export()` on the hot
path (blocks), no per-exporter isolation (one outage = total outage), an
unbounded buffer (OOM under burst), and silent drops (untrustworthy data) —
re-implemented, subtly differently, per team.

## 3. How agents/teams consuming the SDK benefit

- **The agent never waits on, and never dies from, telemetry — for free.**
  *Before:* an agent author has to know to push exports onto a background thread,
  bound the buffer, isolate each backend, and handle flush-on-exit. *After:* they
  call the feat-002 instrumentation API and the pipeline does all of it. Zero
  lines.
- **Adding a second (or fifth) backend is a config line, not new plumbing.**
  *Before:* writing a second exporter thread with its own batching/error handling.
  *After:* list another exporter in config; the same worker fans out to it,
  isolated.
- **A backend outage is invisible.** Kill the Langfuse endpoint mid-run; the run
  completes normally, the OTLP exporter keeps working, and
  `sdk_export_failures_total{exporter="langfuse"}` ticks up so ops can see it.
- **Clean shutdown doesn't lose buffered telemetry.** `force_flush()` /
  `shutdown()` + an `atexit` hook drain the queue on a normal exit — the agent
  author gets at-least-the-buffered-records delivery without writing a shutdown
  handler.
- **Two exporters ship the moment you `configure()`** — `InMemoryExporter` (for
  tests/conformance, feat-011) and `ConsoleExporter` (zero-config dev, FR-12) —
  so an agent emits *something* useful before any backend package is installed.

## 4. Feature specifications

### 4.1 User-facing experience

Most users never touch the pipeline directly — they call feat-002's
instrumentation API and the pipeline runs underneath. The visible surface is:
choosing exporters (usually via config, feat-010), and the two shipped
exporters.

```python
# python — zero-config: records go to the ConsoleExporter, fully async + bounded
import forgesight
forgesight.configure()                              # ConsoleExporter by default (FR-12)

with forgesight.telemetry.agent_run("demo") as run:
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.record_usage(input=100, output=20)
# records were enqueued (non-blocking) and the worker printed them in a batch
```

```python
# python — explicit pipeline control (tests, libraries, advanced wiring)
from forgesight_core import Pipeline, InMemoryExporter, ConsoleExporter

mem = InMemoryExporter()
pipeline = Pipeline(exporters=[mem, ConsoleExporter()])   # fan-out to both, isolated

# ... drive some runs through the runtime bound to this pipeline ...

pipeline.force_flush(timeout_millis=5_000)                # drain; blocking; idempotent
assert mem.get_finished_records()                         # deterministic in tests (feat-011)
pipeline.shutdown()                                        # drain + close exporters; terminal
```

```python
# python — backpressure & drops are observable, never silent
from forgesight_core import Pipeline
pipeline = Pipeline(exporters=[...], max_queue_size=2048)  # default
# under sustained load the newest record is dropped and counted:
#   metric sdk_records_dropped_total increments; a throttled WARN is logged
```

```typescript
// typescript — parity
import { Pipeline, InMemoryExporter, ConsoleExporter } from '@agentforge/sdk-core';

const mem = new InMemoryExporter();
const pipeline = new Pipeline({ exporters: [mem, new ConsoleExporter()] });
await pipeline.forceFlush(5_000);
await pipeline.shutdown();
```

### 4.2 Public API / contract

**Stable (locked)** unless annotated. The knobs and flush/shutdown semantics lock
from v0.1 ([exporter-pipeline §4.8](../design/exporter-pipeline.md#48-configurable-knobs-p8--all-named-all-defaulted)).

#### The pipeline — `forgesight_core/pipeline/pipeline.py` — **stable**

```python
from collections.abc import Sequence
from forgesight_api import Record, TelemetryExporter, Interceptor, ExportResult

class Pipeline:
    def __init__(
        self,
        exporters: Sequence[TelemetryExporter],
        *,
        interceptors: Sequence[Interceptor] = (),
        max_queue_size: int = 2048,
        max_export_batch_size: int = 512,        # ≤ max_queue_size (validated)
        schedule_delay_millis: int = 5_000,
        export_timeout_millis: int = 30_000,
        sample_rate: float = 1.0,                # head-based; 0.0–1.0
    ) -> None: ...

    # hot path — called by the feat-002 runtime; non-blocking, O(#interceptors), no I/O
    def emit(self, record: Record) -> None: ...  # build done upstream; run interceptors → put_nowait

    # drain / lifecycle
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...   # drain + flush each exporter
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...      # force_flush + close; terminal; atexit
```

Behavioural contract (locked):

- `emit()` **never blocks and never does I/O.** It runs the interceptor chain
  then `queue.put_nowait`; on a full queue it drops the *newest* record and
  increments `sdk_records_dropped_total` (NFR-4). Worst case is O(1).
- `force_flush()` drains the queue and calls `force_flush()` on every exporter;
  returns `False` on timeout; **idempotent and non-terminal** (the pipeline stays
  live).
- `shutdown()` is `force_flush()` then `exporter.shutdown()` for each, then stops
  the worker; **idempotent and terminal**; registered via `atexit` so a clean
  process exit doesn't lose buffered records
  ([exporter-pipeline §4.6](../design/exporter-pipeline.md#46-flush--shutdown)).
- Sampling is **head-based** (`TraceIdRatioBased`): the decision is made once per
  trace at the root so a sampled run keeps its *whole* tree and an unsampled run
  emits *nothing*
  ([architecture §9](../design/architecture.md#9-cost--performance-characteristics)).

#### Shipped exporters — `forgesight_core/exporters/` — **stable**

```python
class InMemoryExporter:                          # satisfies TelemetryExporter
    """Collects records in memory for tests + conformance (feat-011)."""
    def export(self, records: Sequence[Record]) -> ExportResult: ...   # appends; SUCCESS
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
    def get_finished_records(self) -> list[Record]: ...   # ordered snapshot
    def clear(self) -> None: ...

class ConsoleExporter:                            # satisfies TelemetryExporter
    """Human-readable / JSON record dump to stdout. The zero-config default (FR-12)."""
    def __init__(self, *, formatter: "Callable[[Record], str] | None" = None,
                 as_json: bool = False) -> None: ...
    def export(self, records: Sequence[Record]) -> ExportResult: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
```

Both implement the locked `TelemetryExporter` SPI from feat-001 — they are the
reference implementations every other exporter (feat-004/012/013/014) is measured
against by the conformance suite (P10, feat-011).

#### Metrics-vs-traces reader split — `forgesight_core/pipeline/metrics.py` — **stable**

```python
class MetricPipeline:
    """Metrics follow OTel's reader model, NOT the queue+worker path.

    Push backends (OTLP, Datadog) use a PeriodicExportingMetricReader;
    pull backends (Prometheus /metrics, feat-012) use a MetricReader.
    Both are fault-isolated and bounded, but they do not share the trace queue.
    """
    def __init__(self, *, export_interval_millis: int = 60_000) -> None: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
```

Trace/record export and metric export are **separate paths**
([exporter-pipeline §4.7](../design/exporter-pipeline.md#47-metrics-vs-traces-split)):
records go through the bounded queue + worker; metrics go through a periodic /
pull reader. The instrument definitions themselves are feat-005; feat-003 owns
only the *reader plumbing* and its isolation.

#### Internal counters this feature emits — **stable names**

| Counter | When |
|---|---|
| `sdk_records_dropped_total` | a record is dropped under backpressure (NFR-4) |
| `sdk_export_failures_total{exporter=…}` | an `export()` returns FAILURE / raises / times out (P6) |
| `sdk_records_sampled_out_total` | a record is dropped by head sampling |

#### TypeScript parity sketch — `@agentforge/sdk-core`

```typescript
export class Pipeline {
  constructor(opts: {
    exporters: TelemetryExporter[];
    interceptors?: Interceptor[];
    maxQueueSize?: number;          // 2048
    maxExportBatchSize?: number;    // 512
    scheduleDelayMillis?: number;   // 5000
    exportTimeoutMillis?: number;   // 30000
    sampleRate?: number;            // 1.0
  });
  emit(record: Record): void;
  forceFlush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<void>;
}
```

Same knobs, defaults, and flush/shutdown semantics; the worker is a background
timer/loop rather than a daemon thread (Node has no GIL constraint), but the
non-blocking, bounded, fault-isolated guarantees are identical
([architecture §10](../design/architecture.md#10-cross-language-parity)).

### 4.3 Internal mechanics

The pipeline implements [`exporter-pipeline.md`](../design/exporter-pipeline.md)
verbatim. The stages:

```
record produced (hot path — agent task/thread; built by feat-002)
   │
   ▼  interceptor chain (hot path)            ← redact / gate content / veto (feat-008)
   │     Record | None     (None ⇒ dropped, sdk_records_dropped_total++)
   │
   ▼  head sampling (hot path)                ← TraceIdRatioBased(sample_rate)
   │     unsampled trace ⇒ drop, sdk_records_sampled_out_total++
   │
   ▼  bounded queue  (max_queue_size, default 2048)
   │     put_nowait; if FULL ⇒ drop NEWEST, sdk_records_dropped_total++  (NFR-4)
   │
   ▼ ───────────────────── thread boundary ─────────────────────
   │
   ▼  export worker  (single daemon; mirrors OTel BatchSpanProcessor — P9)
   │     wait ≤ schedule_delay_millis (5 s) OR until a batch fills
   │     drain ≤ max_export_batch_size (512) records
   │
   ▼  fan-out to each exporter (ISOLATED)     ← FR-11
        for exporter in exporters:
            try:
                result = run_with_timeout(exporter.export, batch, export_timeout_millis)
                if result is FAILURE: sdk_export_failures_total{exporter}++ ; log
            except Exception:                  # defence in depth — export() shouldn't raise (P6)
                sdk_export_failures_total{exporter}++ ; log ; continue   # next exporter unaffected
```

**Why the worker is a thread** (the single justified exception to P9's
"no threads for I/O"): export must survive event-loop stalls and run during
interpreter shutdown, exactly as OTel's `BatchSpanProcessor` does
([exporter-pipeline §4.3](../design/exporter-pipeline.md#43-the-worker),
[design-principles §P9](../design/design-principles.md#p9--async-first-no-threads-for-io-except-the-export-worker)).

**Two layers of fault isolation** (P6,
[exporter-pipeline §4.4](../design/exporter-pipeline.md#44-fault-isolation-p6)):

1. *Per-exporter* — each `export()` is wrapped in try/except + a hard timeout; a
   raise/timeout/`FAILURE` is logged + counted and the loop continues to the next
   exporter. One backend down ⇒ others unaffected.
2. *Per-record* — an interceptor that raises is caught (that interceptor skipped,
   the chain continues); a malformed record never crashes the worker.

`export()` is **contractually non-raising** (it returns `ExportResult.FAILURE`);
the worker still guards with try/except as defence in depth.

**Backpressure = drop-newest, counted** (NFR-4,
[exporter-pipeline §4.5](../design/exporter-pipeline.md#45-backpressure-nfr-4)):
under sustained load where exporters can't keep up, `put_nowait` fails, the SDK
drops the *newest* record, increments `sdk_records_dropped_total`, and logs a
*throttled* WARN. It never blocks the agent and never grows memory unbounded —
and the loss is observable (no silent caps).

**Flush & shutdown** ([exporter-pipeline §4.6](../design/exporter-pipeline.md#46-flush--shutdown)):
`force_flush` drains the queue and flushes each exporter (blocking, idempotent,
non-terminal, `False` on timeout); `shutdown` is `force_flush` + per-exporter
`shutdown()` + stop the worker (idempotent, terminal). An `atexit` hook calls
`shutdown` so a clean exit doesn't drop buffered records; a hard timeout means a
wedged backend can't hang process exit.

### 4.4 Module packaging

- **Lives in:** `forgesight-core` (`forgesight_core`) — the pipeline,
  worker, both shipped exporters, and the metric-reader plumbing are core runtime,
  always installed
  ([architecture §5](../design/architecture.md#5-package-model-three-tiers--integrations)).
  Vendor exporters (feat-004/012/013/014) are *separate* packages that plug into
  this pipeline; none is a dependency of `-core` (P1, NFR-6).
- **Dependencies:** `forgesight-api` (feat-001) + `opentelemetry-api`/`-sdk`
  (for the `TraceIdRatioBased` sampler and the metric reader model — the OTel SDK
  primitives, not any vendor backend) + stdlib. No backend SDK transitively.
- **pip install:**

  ```bash
  pip install forgesight          # the facade pulls -core (the pipeline) transitively
  ```
- **Entry-point group:** exporters that plug into the pipeline register under
  `forgesight.exporters` (the loader is feat-010); the two shipped exporters
  (`console`, `in_memory`) are registered by `-core` itself so they resolve by
  name with no extra install.

### 4.5 Configuration

All knobs are named with documented defaults (P8). Resolved by feat-010
(precedence env → YAML → kwargs, last wins). Constraint: `max_export_batch_size
≤ max_queue_size` (validated at construction; fail fast).

| Field | Env | YAML | Default |
|---|---|---|---|
| `max_queue_size` | `FORGESIGHT_BSP_MAX_QUEUE_SIZE` | `forgesight.pipeline.max_queue_size` | `2048` |
| `max_export_batch_size` | `FORGESIGHT_BSP_MAX_EXPORT_BATCH_SIZE` | `forgesight.pipeline.max_export_batch_size` | `512` |
| `schedule_delay_millis` | `FORGESIGHT_BSP_SCHEDULE_DELAY` | `forgesight.pipeline.schedule_delay_millis` | `5000` |
| `export_timeout_millis` | `FORGESIGHT_BSP_EXPORT_TIMEOUT` | `forgesight.pipeline.export_timeout_millis` | `30000` |
| `sample_rate` | `FORGESIGHT_SAMPLE_RATE` | `forgesight.sample_rate` | `1.0` |
| `metric_export_interval_millis` | `FORGESIGHT_METRIC_EXPORT_INTERVAL` | `forgesight.metrics.export_interval_millis` | `60000` |

```yaml
# forgesight.yaml
forgesight:
  sample_rate: 1.0
  pipeline:
    max_queue_size: 2048
    max_export_batch_size: 512
    schedule_delay_millis: 5000
    export_timeout_millis: 30000
  metrics:
    export_interval_millis: 60000
```

Validation rules: every value is a positive integer (millis/sizes) or a float in
`[0.0, 1.0]` (`sample_rate`); `max_export_batch_size ≤ max_queue_size`; violations
raise at `configure()` — fail fast at bootstrap, never mid-run
([architecture §8](../design/architecture.md#8-failure-modes)).

This feature directly satisfies **NFR-1** (hot-path enqueue < 5 ms, no I/O),
**NFR-2** (all export asynchronous; the hot path never blocks), **NFR-3** (a
failing/slow/misconfigured backend never fails or stalls the agent), and **NFR-4**
(bounded queue + batching + sampling sustain 100k+ runs/day without unbounded
memory).

## 5. Plug-and-play & upgrade story

`forgesight-core` (and hence the pipeline) is always installed — there is no
"add it later." Adding *exporters* to the pipeline is the plug-and-play story, and
it's a `pip install <integration>` + one config line (P2): the new exporter
resolves by entry-point name (feat-010) and the same worker fans out to it,
isolated.

Upgrade safety: the knobs, the flush/shutdown semantics, and the two shipped
exporters are stable from v0.1 (P5,
[exporter-pipeline §6](../design/exporter-pipeline.md#6-migration--rollout)). New
knobs arrive as minor bumps with safe defaults; tightening a default or removing a
knob is a major bump + ADR.

## 6. Cross-language parity

**Identical:** the pipeline stages, the five knobs + their defaults, head-based
sampling semantics, the two-layer fault isolation, drop-newest backpressure with
counted drops, flush/shutdown semantics + atexit, the metrics-vs-traces split,
and the two shipped exporters
([architecture §10](../design/architecture.md#10-cross-language-parity)).

**Allowed to differ:** the worker is a daemon **thread** in Python (mirroring
`BatchSpanProcessor`, surviving event-loop stalls + interpreter shutdown) and a
background **timer/microtask loop** in Node (no GIL, single-threaded runtime);
`atexit` vs `process.on('exit'/'beforeExit')`; `queue.Queue` vs an array-backed
bounded ring. The guarantees are identical; the concurrency primitive differs.

**Staging:** Python in 0.1; TypeScript reaches this surface on its 0.2/0.4 line
per [ADR-0008](../adr/0008-python-first-multilanguage-parity.md).

## 7. Test strategy

- **Unit** — `emit()` does no I/O and returns in O(1); a full queue drops the
  newest record and increments `sdk_records_dropped_total`; batching respects
  `max_export_batch_size` and `schedule_delay_millis`; `max_export_batch_size >
  max_queue_size` raises at construction.
- **Fault isolation (load-bearing, P6)** — an exporter that *raises*, one that
  *times out*, and one that returns `FAILURE` each leave sibling exporters and the
  agent unaffected; `sdk_export_failures_total{exporter}` ticks for the bad one
  only. Mirrors the AgentForge `feat-009` "raising hook doesn't crash the run"
  test.
- **Backpressure** — under sustained over-production memory stays bounded by
  `max_queue_size`; drops are counted and a throttled WARN fires (no silent loss).
- **Flush/shutdown** — `force_flush` drains everything and is idempotent;
  `shutdown` drains + closes + is terminal + idempotent; a wedged backend can't
  hang `shutdown` past its timeout; the `atexit` hook fires on clean exit.
- **Sampling** — `sample_rate=0.0` emits nothing, `1.0` emits everything, and a
  sampled trace keeps its *whole* tree (head decision at the root).
- **Metrics-vs-traces** — the metric reader path is independent of the trace
  queue; killing the trace path doesn't stop metrics and vice-versa.
- **Conformance** — `InMemoryExporter` and `ConsoleExporter` pass the feat-011
  exporter conformance suite; that suite is the bar every vendor exporter must
  clear (P10).
- **Worker supervision** — a worker that dies is restarted; a health metric
  reflects it ([exporter-pipeline §7](../design/exporter-pipeline.md#7-risks)).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Drops hidden from operators | `sdk_records_dropped_total` metric + a throttled WARN + a `get`-able counter; no silent caps. |
| Worker dies silently | Supervised restart + a health metric; a conformance test kills/restarts the worker ([exporter-pipeline §7](../design/exporter-pipeline.md#7-risks)). |
| `shutdown` hangs on a wedged backend | Hard `export_timeout_millis`; `shutdown` returns rather than blocking exit indefinitely. |
| One slow exporter starves the batch window for fast ones | Deferred — single worker + per-exporter try/except is the v0.1 design ([ADR-0003](../adr/0003-async-fault-isolated-export-pipeline.md)); per-exporter sub-queues are an open question revisited only if measured ([exporter-pipeline §8](../design/exporter-pipeline.md#8-open-questions)). |
| Retrying failed exports | Out of scope — backends/collectors own durability ([exporter-pipeline §3](../design/exporter-pipeline.md#3-non-goals)); the SDK drops, it does not persist-and-retry. |
| Thread + `contextvars` interaction | The record is fully built (frozen) before crossing the queue (feat-002), so the worker thread never reads task-local state — no propagation needed across the boundary. |
| Tail-based sampling | Out of scope — a collector concern; the SDK does head-based only ([exporter-pipeline §3](../design/exporter-pipeline.md#3-non-goals)). |

## 9. Out of scope

- **The OTLP exporter and the GenAI semconv mapping** — feat-004. feat-003 ships
  the *pipeline* and the in-memory/console exporters; turning records into OTLP
  spans is a separate package.
- **Metric instrument definitions** — feat-005 defines the counters/histograms;
  feat-003 owns only the *reader plumbing* and its isolation.
- **Interceptor implementations** (redaction, content gating, policy) — feat-008.
  The pipeline *runs* the chain; it doesn't implement any interceptor.
- **Vendor exporters** (Prometheus/Langfuse/ClickHouse/Datadog) — feat-012/013/
  014/015. They plug into this pipeline.
- **Config loading / `configure()`** — feat-010. feat-003 *reads* the resolved
  config; it doesn't parse env/YAML.
- **Persist-and-retry on export failure** and **tail-based sampling** — both
  explicit non-goals ([exporter-pipeline §3](../design/exporter-pipeline.md#3-non-goals)).

## 10. References

- [`exporter-pipeline.md`](../design/exporter-pipeline.md) — the design this
  feature implements (stages, hot path, worker, fault isolation, backpressure,
  flush/shutdown, metrics-vs-traces, knobs)
- [`architecture.md`](../design/architecture.md) §7 (lifecycle), §8 (failure
  modes), §9 (cost & performance characteristics)
- [`design-principles.md`](../design/design-principles.md) — P6, P8, P9
- [`requirements.md`](../requirements.md) — NFR-1, NFR-2, NFR-3, NFR-4; FR-11
- [ADR-0003](../adr/0003-async-fault-isolated-export-pipeline.md) — async,
  bounded, fault-isolated export pipeline
- Depends on: [feat-001](./feat-001-core-domain-model-and-contracts.md)
  (`Record`, `TelemetryExporter`, `Interceptor`, `ExportResult`). Blocks:
  feat-004 (OTLP exporter), feat-012 (Prometheus), feat-013 (Langfuse), feat-014
  (ClickHouse). Fed by: [feat-002](./feat-002-telemetry-runtime-and-instrumentation-api.md)
  (which calls `emit()`).
- Prior art: OpenTelemetry `BatchSpanProcessor` / `SpanExporter` /
  `PeriodicExportingMetricReader` (the proven shape this adopts);
  <https://opentelemetry.io/docs/specs/otel/trace/sdk/> ("`export` MUST NOT
  throw"); AgentForge `feat-009-observability` (hook fault isolation precedent).
