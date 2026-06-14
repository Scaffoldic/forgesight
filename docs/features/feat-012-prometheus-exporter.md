# feat-012: Prometheus exporter

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-012 |
| **Title** | Prometheus exporter — pull-based `/metrics` (MetricReader) + push-gateway |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | both |
| **Module package(s)** | `forgesight-prometheus` |
| **Depends on** | feat-005 |
| **Blocks** | none |

---

## 1. Why this feature

Prometheus is the default metrics backend for most teams already running
Kubernetes, and it is *pull-based* — a scraper hits a `/metrics` endpoint on a
schedule, the opposite of the push pipeline the SDK uses for traces. An agent
team that wants "how many runs, how many failures, how much have we spent, how
slow is p99" on their existing Grafana dashboards hits three problems today:

- The SDK's product metrics (`agent_runs_total`, `agent_failures_total`,
  `agent_cost_total`, `agent_duration_ms`, `tool_invocations_total`,
  `mcp_invocations_total`) and the GenAI histograms (`gen_ai.client.token.usage`,
  `gen_ai.client.operation.duration`) live in the SDK's OTel meter — they have to
  be bridged onto a Prometheus registry and a scrape endpoint.
- A short-lived agent (a CI job, a one-shot batch run) is never scraped before it
  exits, so its metrics are lost unless they are *pushed* to a Pushgateway.
- Mapping OTel instruments to Prometheus metric types (counter vs histogram),
  sanitising attribute keys into label names, and keeping label cardinality sane
  is fiddly, easy to get wrong, and easy to get *expensively* wrong (a `run_id`
  label melts Prometheus).

This package gives a team a `/metrics` endpoint (and an optional Pushgateway
path) with the SDK's metrics already mapped, labelled, and cardinality-guarded —
a `pip install` and one config block.

## 2. Why this belongs in the SDK ecosystem (vs each team integrating the backend by hand)

- **The metric inventory is the SDK's, not the team's.** The exact instrument
  names, units, attribute sets, and histogram bucket boundaries are fixed by
  feat-005 and the GenAI semconv mapping (`otel-semantic-conventions.md` §4.4).
  If every team bridges those to Prometheus by hand, the bucket boundaries drift,
  the label names diverge, and two teams' "p99 latency" stop being comparable —
  which defeats the platform-wide comparability that is the SDK's reason to exist
  (requirements §1.1). Shipping the bridge once makes every team's
  `agent_*` series identical.
- **Cardinality safety is a contract, not a per-team discovery.** The line between
  a useful label (`provider`, `model`, `agent.name`, `status`) and a
  Prometheus-killing one (`run_id`, `trace_id`, free-form `metadata.*`) is a
  property of the SDK's domain model. The package encodes a safe default label
  allow-list so a team can't accidentally export a per-run-id counter. Left to
  each team, the first cardinality incident is a production outage.
- **It must hold the foundation invariants.** Like every exporter it implements
  the `TelemetryExporter` Protocol (`architecture.md` §4.2), runs on the pipeline
  worker, never on the hot path, and is fault-isolated: a wedged Pushgateway is
  caught, counted in `sdk_export_failures_total`, and never stalls the agent
  (P6 / NFR-3). A hand-rolled exporter usually gets the isolation wrong and blocks
  a run on a slow scrape target.
- **Anti-pattern it prevents:** the copy-pasted `prometheus_client.Counter`
  sprinkled through agent code, incremented inline, with ad-hoc label sets — the
  exact bespoke-glue rot the SDK exists to replace (requirements §1.1).

This is *not* something the OTLP keystone covers for free. A backend that ingests
OTLP gets metrics through `forgesight-otel` with no dedicated package
(`architecture.md` §2). Prometheus is the opposite shape — **pull**, with its own
registry and exposition format — so it earns a first-party package, exactly as
the architecture's package model anticipates (`architecture.md` §5).

## 3. How consuming agents/teams benefit

- **Before:** an agent team writes ~80 lines wiring `prometheus_client`, defines
  six counters and two histograms by hand, picks bucket boundaries that don't
  match anyone else's, increments them inline in the run loop, stands up an HTTP
  server, and discovers six weeks later that the `run_id` label has created two
  million series.
- **After:** `pip install forgesight-prometheus`, add `prometheus` to the
  exporters list, point Prometheus at `:9464/metrics`. Every `agent_*` series and
  GenAI histogram appears, labelled and cardinality-bounded, with bucket
  boundaries identical to every other team's.
- **Short-lived agents are covered without code change.** Set
  `push_gateway: http://pushgateway:9091` and a CI / batch run flushes its
  metrics to the gateway on shutdown — same config block, no inline pushing.
- **Swapping is a config line.** A team on Prometheus today that wants
  Datadog tomorrow drops `prometheus`, adds `datadog` — no agent code change
  (requirements §10.4).
- **One process, many backends.** Prometheus for ops dashboards *and*
  `forgesight-otel` to the org collector *and* a custom exporter — all from
  the same run, fanned out by the pipeline (FR-11).

## 4. Feature specifications

### 4.1 User-facing experience

```bash
pip install forgesight-prometheus
```

```python
# python
import forgesight

# Resolved from the exporters list — preferred (entry point: "prometheus").
forgesight.configure()      # reads forgesight.yaml / FORGESIGHT_* env

# Or construct + register explicitly.
from forgesight_prometheus import PrometheusExporter

exporter = PrometheusExporter(host="0.0.0.0", port=9464, prefix="agentforge")
forgesight.configure(exporters=[exporter])

# Pull: Prometheus scrapes http://<host>:9464/metrics
# Push (short-lived runs): set push_gateway=... below.
```

```yaml
# forgesight.yaml — preferred
exporters:
  - name: prometheus
    config:
      host: "0.0.0.0"
      port: 9464
      prefix: "agentforge"
      # push_gateway: "http://pushgateway:9091"   # opt-in, for short-lived runs
```

```typescript
// typescript
import { configure } from '@agentforge/sdk';
import { PrometheusExporter } from '@agentforge/sdk-prometheus';

configure({ exporters: [new PrometheusExporter({ host: '0.0.0.0', port: 9464, prefix: 'agentforge' })] });
```

### 4.2 Public API / contract

`PrometheusExporter` implements the locked `TelemetryExporter` Protocol
(`architecture.md` §4.2) and is discovered via the entry point group
`forgesight.exporters` under the name `prometheus`. It must pass the exporter
conformance suite (feat-011).

```python
# forgesight_prometheus/exporter.py
from collections.abc import Sequence
from forgesight_api import Record, ExportResult, TelemetryExporter

class PrometheusExporter(TelemetryExporter):
    """Bridges SDK metrics onto a Prometheus registry + a pull /metrics endpoint
    (and an optional Pushgateway). Stable from v0.2."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 9464,
        prefix: str = "agentforge",
        push_gateway: str | None = None,
        push_job: str = "forgesight",
        label_allowlist: tuple[str, ...] = (
            "agent_name", "provider", "model", "status",
            "operation", "tool_name", "environment", "team",
        ),
        registry: "CollectorRegistry | None" = None,   # prometheus_client
    ) -> None: ...

    # --- TelemetryExporter Protocol (locked) ---
    def export(self, records: Sequence[Record]) -> ExportResult: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...   # pushes if gateway set
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...      # final push + stop HTTP server
```

`export()` folds each record's metric contributions into the registry's
instruments and **returns** `SUCCESS`/`FAILURE` — it never raises (P6). The HTTP
server is started lazily on first `export` (or eagerly via a flag); the
Pushgateway push happens on `force_flush` / `shutdown` and, optionally, on an
interval.

**Stability:** the class name, constructor keywords, and config keys are stable
from v0.2. The internal instrument registry is private.

### 4.3 Internal mechanics

The SDK's metric path is OTel's reader model (`exporter-pipeline.md` §4.7), so
this exporter is fundamentally a **`MetricReader`** with a Prometheus exposition
collector on top — the same shape as `opentelemetry-exporter-prometheus`. It does
**not** sit on the trace queue/worker; it derives metrics from records and serves
them on scrape.

```
SDK meter (feat-005)  ──► metric records ──► PrometheusExporter
                                              │  fold into prometheus_client
                                              │  CollectorRegistry instruments
                                              ▼
   pull:  GET :9464/metrics  ◄── Prometheus scraper   (WSGI/ASGI exposition)
   push:  force_flush/shutdown ──► push_to_gateway(push_gateway, job=push_job)
```

**OTel instrument → Prometheus type mapping** (units in the metric name, OTel
convention):

| SDK / GenAI instrument | Prometheus name | Type |
|---|---|---|
| `agent_runs_total` | `{prefix}_agent_runs_total` | Counter |
| `agent_failures_total` | `{prefix}_agent_failures_total` | Counter |
| `agent_cost_total` (USD) | `{prefix}_agent_cost_usd_total` | Counter |
| `agent_duration_ms` | `{prefix}_agent_duration_milliseconds` | Histogram |
| `tool_invocations_total` | `{prefix}_tool_invocations_total` | Counter |
| `mcp_invocations_total` | `{prefix}_mcp_invocations_total` | Counter |
| `gen_ai.client.token.usage` | `{prefix}_gen_ai_client_token_usage` | Histogram (label `gen_ai_token_type`) |
| `gen_ai.client.operation.duration` | `{prefix}_gen_ai_client_operation_duration_seconds` | Histogram |

Histogram bucket boundaries are taken verbatim from `otel-semantic-conventions.md`
§4.4 (token usage and duration buckets) so cross-team comparability holds.

**Label mapping.** Span/record attributes become Prometheus labels after: (1)
dot→underscore sanitisation (`gen_ai.provider.name` → `gen_ai_provider_name`,
`agent.name` → `agent_name`); (2) filtering through `label_allowlist`. Anything
outside the allow-list is dropped from the label set (still available on traces
via OTLP). Counters whose name ends `_total` follow the Prometheus convention.

**Cardinality guidance (load-bearing).**

- **Allowed as labels:** bounded-domain attributes — `agent_name`, `provider`,
  `model`, `status` (six-value enum), `operation`, `tool_name`, plus the
  governance dimensions teams actually slice on (`environment`, `team`).
- **Never labels:** `run_id`/`gen_ai_agent_id` (ULID — unbounded),
  `trace_id`, `context_id`, free-form `metadata.*`. These are high-cardinality and
  would create one series per run. The allow-list excludes them by default and a
  WARN is logged (throttled) if a caller adds one.
- **Model** is bounded in practice but can sprawl across dated snapshots; the
  default keeps it, documented as the cardinality knob to watch.

### 4.4 Module packaging

This is an **integration package — one backend, one vendor SDK**
(`architecture.md` §5). It wraps exactly **one** vendor SDK: `prometheus-client`.
Per P1, this dependency is **never** added to `forgesight-core` — it lives
only here.

| Package | Provides | Deps |
|---|---|---|
| `forgesight-prometheus` | `PrometheusExporter` (pull `/metrics` + push-gateway) | `forgesight-core`, `prometheus-client` |

```toml
# forgesight_prometheus/pyproject.toml
[project]
dependencies = ["forgesight-core>=0.2", "prometheus-client>=0.20"]

[project.entry-points."forgesight.exporters"]
prometheus = "forgesight_prometheus.exporter:PrometheusExporter"
```

Installing the package makes `prometheus` resolvable by name from config
(`architecture.md` §6, extension path 1). No core change.

### 4.5 Configuration

Read from `forgesight.yaml` (`exporters[].config`) and `FORGESIGHT_*`
env, constructor kwargs win (FR-12). Every knob is named and defaulted (P8).

| Key | Env | Default | Validation |
|---|---|---|---|
| `host` | `FORGESIGHT_PROMETHEUS_HOST` | `0.0.0.0` | bind address |
| `port` | `FORGESIGHT_PROMETHEUS_PORT` | `9464` | 1–65535 (OTel Prometheus default) |
| `prefix` | `FORGESIGHT_PROMETHEUS_PREFIX` | `agentforge` | valid Prometheus name prefix (`[a-zA-Z_][a-zA-Z0-9_]*`) |
| `push_gateway` | `FORGESIGHT_PROMETHEUS_PUSH_GATEWAY` | `null` | URL; when set, pull endpoint still serves unless `port: 0` |
| `push_job` | `FORGESIGHT_PROMETHEUS_PUSH_JOB` | `forgesight` | Pushgateway `job` label |
| `label_allowlist` | — | see §4.2 | list of sanitised attribute names |

Validation: `prefix` rejected if not a legal Prometheus name; setting a
known-high-cardinality attribute (`run_id`, `trace_id`) in `label_allowlist`
logs a throttled WARN but is honoured (operator override). `port: 0` disables the
pull endpoint (push-only mode).

## 5. Plug-and-play & upgrade story

Add later with `pip install forgesight-prometheus` + the `exporters` block —
no agent-code change (P2). Remove by dropping the package + config entry. The
class name, constructor keywords, and config keys are stable from v0.2; new knobs
arrive as optional kwargs with defaults (P5). Histogram bucket boundaries track
the GenAI semconv mapping; a bucket change is a feat-005 / mapping-version event,
surfaced via `semconv_version`, not a silent break.

## 6. Cross-language parity

Identical across Python / TypeScript: the instrument→Prometheus-type table, the
metric names, the histogram buckets, the label allow-list, and the config keys
(`architecture.md` §10). Allowed to differ: the vendor library (`prometheus-client`
in Python; `prom-client` in TS), the HTTP exposition server idiom (WSGI/ASGI vs
Node `http`), and naming. TypeScript targets parity by 0.4.

## 7. Test strategy

- **Unit:** instrument→Prometheus-type mapping; attribute→label sanitisation;
  allow-list filtering drops `run_id`/`trace_id`; counter `_total` suffix rules;
  bucket boundaries equal the semconv tables.
- **Conformance (feat-011):** runs the exporter conformance suite — `export()`
  returns a result and never raises; `force_flush` / `shutdown` are idempotent;
  fault isolation (a broken Pushgateway is caught + counted, never raised).
- **Integration:** scrape the live `/metrics` endpoint and assert the exposition
  parses and carries the expected series + labels; push to a local Pushgateway
  container and assert delivery on `shutdown`.
- **Cardinality:** a run loop with N distinct `run_id`s produces a *bounded*
  series count (no per-run-id series).
- **Example agent:** a short-lived run with `push_gateway` set; assert metrics
  land on the gateway after exit.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| `run_id`/`metadata.*` blowing up cardinality | Default label allow-list excludes them; WARN on override (§4.3). |
| Pull endpoint never scraped for short-lived runs | Pushgateway path on `force_flush`/`shutdown` (§4.3). |
| Pushgateway slow/down stalling shutdown | `export`/push wrapped + timed (`export_timeout`); failure counted, never raised (P6). |
| Port clash with an existing exporter in-process | Configurable `port`; `port: 0` → push-only; documented. |
| Counter reset semantics across process restarts | Standard Prometheus counter semantics; Pushgateway `job`/`instance` grouping documented. |
| Two exporters wanting one registry | Optional `registry=` kwarg to share a `CollectorRegistry`. |

## 9. Out of scope

- **Building Grafana dashboards.** We expose series; dashboards live in the
  backend (requirements §11).
- **Alerting rules.** Metrics are emitted; alerts are configured in the user's
  Prometheus/Alertmanager stack.
- **Trace export.** Prometheus is metrics-only; traces go through
  `forgesight-otel`.
- **Remote-write / OTLP-metrics-to-Prometheus.** Out of scope here; that path is
  the collector's job and is reachable via `forgesight-otel`.
- **Per-run-id metrics.** Deliberately impossible by default — that is what
  traces are for.

## 10. References

- [`../design/architecture.md`](../design/architecture.md) §2 (keystone), §4.2 (SPI), §5 (package model)
- [`../design/design-principles.md`](../design/design-principles.md) P1, P2, P6, P8, P10
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.7 (metrics reader model)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.4 (metric instruments + buckets)
- [`../requirements.md`](../requirements.md) FR-6, FR-11, NFR-3
- feat-005 (metrics & instruments), feat-011 (conformance harness)
- Prior art: `opentelemetry-exporter-prometheus`, `prometheus-client`, OTel Prometheus default port 9464
