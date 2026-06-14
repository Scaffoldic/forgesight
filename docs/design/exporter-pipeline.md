# Design Doc: The export pipeline

## Metadata

| Field | Value |
|---|---|
| **Title** | Async, bounded, fault-isolated export pipeline |
| **Status** | accepted |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Related features** | feat-002, feat-003, feat-004 |

---

## 1. Context

NFR-1/2/3/4 and P6 demand that telemetry export be fast on the hot path, never block
the agent, never fail the agent, and never grow memory unbounded — while fanning out
to several backends at once (FR-11). OpenTelemetry's own SDK already solves this shape
with `SpanProcessor` / `SpanExporter` / `BatchSpanProcessor`. We adopt that proven
design rather than invent one.

## 2. Goals

- Hot-path enqueue in **< 5 ms** with no I/O (NFR-1/2).
- One slow/failing/misconfigured backend affects **nothing** else (P6, NFR-3).
- Bounded memory under sustained load; **drop, don't grow** (NFR-4).
- Graceful `force_flush()` / `shutdown()` that don't lose buffered records on clean
  exit.

## 3. Non-goals

- Retrying failed exports to disk (out of scope; backends/collectors own durability).
- Tail-based sampling (collector concern; we do head-based — see otel mapping §4.5).

## 4. Proposal

### 4.1 Stages

```
record produced (hot path, agent thread/task)
   │  build immutable Record
   ▼
interceptor chain (hot path)            ← redact / gate content / veto (feat-008)
   │  Record | None   (None ⇒ dropped, counted)
   ▼
bounded queue  (max_queue_size, default 2048)
   │  put_nowait; if full ⇒ drop newest, increment sdk_records_dropped_total (NFR-4)
   ▼ ─────────────────── thread boundary ───────────────────
export worker (single background worker; mirrors BatchSpanProcessor)
   │  drain up to max_export_batch_size (default 512) every schedule_delay
   │  (default 5 s) or when a batch fills
   ▼
fan-out to each exporter (isolated)      ← FR-11
   for exporter in exporters:
       try: result = exporter.export(batch)         # returns SUCCESS|FAILURE, never raises
       except Exception: log + count; continue       # P6 isolation
```

### 4.2 Hot path is non-blocking

`on record` does exactly: build the record → run interceptors → `queue.put_nowait`.
No network, no lock held across I/O, no awaiting an exporter. Worst case (queue full)
is a `put_nowait` failure → drop + counter increment, still O(1). This is what keeps
NFR-1/NFR-2.

### 4.3 The worker

A single daemon worker (thread in Python, mirroring OTel's `BatchSpanProcessor`;
justified by P9 because it must survive event-loop stalls and run during interpreter
shutdown). It:

- waits up to `schedule_delay`,
- drains the queue in batches of ≤ `max_export_batch_size`,
- calls every registered exporter with the batch,
- wraps each `export()` in try/except, honouring `export_timeout`,
- records `sdk_export_failures_total{exporter=…}` and `sdk_records_dropped_total`.

### 4.4 Fault isolation (P6)

Two layers:

1. **Per-exporter:** each `export()` is wrapped; a raise/timeout/`FAILURE` is logged +
   counted; the loop continues to the next exporter. One backend down ⇒ others
   unaffected.
2. **Per-record path:** an interceptor raising is caught (that interceptor skipped,
   chain continues); a malformed record never crashes the worker.

`export()` is **contractually non-raising** — it returns `ExportResult.FAILURE`. The
worker still guards with try/except as defence in depth.

### 4.5 Backpressure (NFR-4)

The queue is bounded. Under sustained load where backends can't keep up, the SDK
**drops the newest record** and increments a counter — it never blocks the agent and
never grows memory without bound. Drops are observable (a metric + a throttled WARN)
so silent loss is impossible (the "no silent caps" rule).

### 4.6 Flush & shutdown

- `force_flush(timeout)` — drain the queue and flush every exporter; blocking; returns
  `False` on timeout; non-terminal (idempotent, pipeline stays live).
- `shutdown(timeout)` — `force_flush` then `exporter.shutdown()` for each, then stop
  the worker; idempotent; terminal. Registered via `atexit` so a clean process exit
  doesn't lose buffered records.

### 4.7 Metrics-vs-traces split

Traces use the queue+worker+exporter path above. Metrics follow OTel's reader model:
a `PeriodicExportingMetricReader` (push backends: OTLP, Datadog) or a pull
`MetricReader` (Prometheus `/metrics`). The SDK exposes both; the metric path is also
fault-isolated and bounded.

### 4.8 Configurable knobs (P8 — all named, all defaulted)

| Field | Env | Default |
|---|---|---|
| `max_queue_size` | `FORGESIGHT_BSP_MAX_QUEUE_SIZE` | 2048 |
| `max_export_batch_size` | `FORGESIGHT_BSP_MAX_EXPORT_BATCH_SIZE` | 512 |
| `schedule_delay_millis` | `FORGESIGHT_BSP_SCHEDULE_DELAY` | 5000 |
| `export_timeout_millis` | `FORGESIGHT_BSP_EXPORT_TIMEOUT` | 30000 |
| `sample_rate` | `FORGESIGHT_SAMPLE_RATE` | 1.0 |

Constraint: `max_export_batch_size ≤ max_queue_size`.

## 5. Alternatives considered

| Option | Why not |
|---|---|
| Multiplexing exporter (one queue, fan-out inside a single exporter) | A slow backend slows batching for all; less isolation. We use one worker that iterates exporters with per-exporter guards instead. |
| One worker per exporter | More isolation but N threads + N queues; overkill at our scale; the per-exporter try/except already isolates failures. Reconsider if a backend needs independent backpressure. |
| Synchronous `SimpleSpanProcessor`-style export | Blocks the hot path on I/O; fails NFR-1/2. Offered only as a debug/console exporter. |
| Unbounded queue | Fails NFR-4; an OOM under backend outage is worse than dropping. |

## 6. Migration / rollout

Lands in feat-003 as the core pipeline; feat-004+ exporters plug into it. The knobs
are stable from v0.1.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Drops hidden from operators | Counter + throttled WARN + a `sdk_records_dropped_total` metric. |
| Worker dies silently | Supervised restart + a health metric; conformance test kills/restarts. |
| `shutdown` hangs on a wedged backend | Hard `timeout`; return `False`; never block exit indefinitely. |

## 8. Open questions

1. Should a single exporter get its own bounded sub-queue so a slow one can't starve
   the batch window for fast ones? *(defer; revisit if measured.)*
2. Expose a synchronous, inline mode for tests beyond the in-memory exporter? *(the
   in-memory exporter + a deterministic flush should suffice — feat-011.)*

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-06-14 | Adopt OTel BatchSpanProcessor shape | Proven; matches P6/NFR-1..4 exactly |
| 2026-06-14 | Single worker, per-exporter try/except isolation | Simplest design that meets isolation at our scale |
| 2026-06-14 | Bounded queue, drop-newest under backpressure | Memory safety > completeness on outage |

## 10. References

- OTel SDK spec (SpanProcessor / SpanExporter / BatchSpanProcessor; error-handling
  "MUST NOT throw"): <https://opentelemetry.io/docs/specs/otel/trace/sdk/>
- [`architecture.md`](./architecture.md) §7, [`design-principles.md`](./design-principles.md) P6/P9
- feat-002, feat-003
