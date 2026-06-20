# Export pipeline runbook

> The async, bounded, fault-isolated path that takes every record off the hot path and fans it out to your exporters without ever blocking or crashing your agent. **Spec:** [feat-003](../features/feat-003-async-export-pipeline.md)

## What it does

The export pipeline is the heart of the runtime (`forgesight_core.processor`). The hot path (`emit_record`) is non-blocking: it samples, runs the interceptor chain, and enqueues the record into a **bounded** queue — no I/O, no awaiting an exporter. A single background daemon worker drains that queue in batches and fans out to every registered exporter, each `export()` call fault-isolated so one slow or failing backend never affects the agent or the other exporters. Under sustained backpressure the queue drops the newest record and counts it rather than growing unbounded. The design mirrors OpenTelemetry's `BatchSpanProcessor`.

## When to use it

- Always — it is the core dispatch path; everything you emit goes through it.
- Tune its knobs when you have high-throughput loops, memory constraints, or want a longer/shorter export window.
- Switch to synchronous mode in tests and simple scripts where you want deterministic, inline export.

## Install

Built into `forgesight-core` — nothing to install. It is the runtime you get from `forgesight.configure(...)`.

## Set up / Configure

The pipeline is tuned through `forgesight.configure(...)` (or the equivalent `forgesight.yaml` / `FORGESIGHT_*` env). All knobs are named and defaulted (P8):

| Knob (kwarg / `RuntimeConfig`) | Default | What it does |
|---|---|---|
| `max_queue_size` | `2048` | Bounded queue capacity; full ⇒ drop newest |
| `max_export_batch_size` | `512` | Max records drained per export batch (must be ≤ `max_queue_size`) |
| `schedule_delay_millis` | `5000` | How often the worker wakes to drain |
| `export_timeout_millis` | `30000` | Per-export timeout budget |
| `sample_rate` | `1.0` | Head sampling in `[0.0, 1.0]`; sampled-out records are counted |
| `sync_export` | `False` | Inline synchronous export vs. the async background worker |

```python
import forgesight

# Production: async worker, batched, sampled.
forgesight.configure(
    service_name="my-agent",
    exporters=["otel"],
    max_queue_size=4096,
    max_export_batch_size=512,
    schedule_delay_millis=5000,
    sample_rate=0.25,
)
```

```python
# Tests / simple scripts: inline, deterministic — no worker, no timing.
forgesight.configure(exporters=["in-memory"], sync_export=True)
```

Set `sync_export=True` in unit tests and one-shot scripts so each `emit_record` exports immediately and assertions see records without flushing. Leave it `False` (async) in production so the agent thread never blocks on export I/O. Constraint: `max_export_batch_size` must not exceed `max_queue_size`, and `sample_rate` must be in `[0.0, 1.0]` — both are validated at config time.

## Behavior

- **Queue → batch → export.** `emit_record` samples by `trace_id`, runs interceptors, then `put_nowait` into the bounded queue. The background worker (`forgesight-export-worker`, a daemon thread) waits up to `schedule_delay_millis`, drains up to `max_export_batch_size` records, and calls every exporter under one export lock.
- **Backpressure / drop-on-full.** When the queue is full, `put_nowait` raises `queue.Full`; the record is dropped, `runtime.dropped` is incremented, and a throttled WARN is logged — the agent is never blocked and memory never grows unbounded.
- **Sampling.** Records that sample out increment `runtime.sampled_out` and never enqueue (metrics still count all records). An unparseable `trace_id` is never silently dropped — it samples in.
- **Fault isolation (P6).** Each `export()` is wrapped: a raise, a timeout, or a returned `ExportResult.FAILURE` is logged and increments `runtime.export_failures`, then the loop continues to the next exporter. One bad backend can't break the run or the others. An interceptor that raises is caught and skipped (a deliberate `GovernanceSignal` is allowed to propagate). `export()` is contractually non-raising — it returns `FAILURE`, never raises — and the worker guards with try/except as defence in depth.
- **Flush & shutdown.** `force_flush(timeout_millis)` drains the queue and flushes every exporter (blocking, idempotent, non-terminal, returns `False` on timeout). `shutdown(timeout_millis)` stops the worker, drains, and shuts down every exporter (idempotent, terminal); it is registered via `atexit` so a clean process exit doesn't lose buffered records.

## Operate it

To confirm flushing and inspect counters:

1. **Force a flush.** Before a short-lived process exits, call `forgesight.force_flush()` (or `shutdown()`); confirm it returns `True`. `atexit` also calls `shutdown()` on a clean exit, but explicit flushing is safest.
2. **Read the counters on the runtime** (`forgesight_core.get_runtime()`):
   - `runtime.dropped` — records dropped by a full queue or an interceptor veto (watch this for saturation)
   - `runtime.export_failures` — exporters that raised or returned `FAILURE`
   - `runtime.sampled_out` — records dropped by head sampling
   - `runtime.listener_errors` — event listeners that raised
3. **Watch the logs** under loggers `forgesight.pipeline` (queue-full WARN, export FAILURE/raise) — a rising `export queue full` WARN means you're saturated.
4. **In tests**, set `sync_export=True` + the `in-memory` exporter and assert directly on captured records without timing.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Records missing at process exit | Process exited before the worker drained | Call `force_flush()` / `shutdown()`; rely on the `atexit` hook for clean exits |
| `runtime.dropped` climbing, `export queue full` WARNs | Queue saturated — backends can't keep up | Raise `max_queue_size`, increase `max_export_batch_size`, lower `schedule_delay_millis`, or lower `sample_rate` |
| Records missing but no drops | Sampled out | Check `runtime.sampled_out`; raise `sample_rate` toward `1.0` |
| `runtime.export_failures` rising | A backend is down/misconfigured | Fix that exporter — by design it can't break the run or the others (fault isolation, P6) |
| Agent stalls on emit | Should never happen — `emit_record` is non-blocking and `export()` never raises | If you see blocking, you're in `sync_export=True`; switch to async for production |
| `ValueError: max_export_batch_size must not exceed max_queue_size` | Misconfigured knobs | Keep `max_export_batch_size ≤ max_queue_size` |
| `force_flush` returns `False` | An exporter timed out | Raise `export_timeout_millis`, or accept partial flush on a wedged backend |

## Reference

- Feature spec: [feat-003](../features/feat-003-async-export-pipeline.md)
- Design doc: [exporter-pipeline.md](../design/exporter-pipeline.md)
- Source: [`packages/forgesight-core/src/forgesight_core/processor.py`](../../packages/forgesight-core/src/forgesight_core/processor.py)
- Playbooks: [01-install.md](../playbooks/01-install.md) · [02-instrument-your-agent.md](../playbooks/02-instrument-your-agent.md)
