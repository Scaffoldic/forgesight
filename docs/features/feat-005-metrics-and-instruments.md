# feat-005: Metrics & instruments

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-005 |
| **Title** | Metrics & instruments (FR-6 product metrics + GenAI histograms) |
| **Status** | `shipped` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` |
| **Depends on** | feat-002 |
| **Blocks** | feat-012 (Prometheus) |

---

## 1. Why this feature

Traces answer "what did *this* run do?" Metrics answer "how is the *fleet* doing?" —
how many runs, how many failed, how much did they cost, how long did they take, how
many tool and MCP calls. Those are the numbers on every agent dashboard and every
FinOps report, and they are the questions you cannot answer from spans alone at scale
(you can't sum a million traces in a dashboard query).

The pain without this feature: each team hand-defines a `Counter` here, a `Histogram`
there, picks its own units (`ms` vs `s`), its own bucket boundaries, its own attribute
keys — and then nobody's "p99 latency" or "cost per run" means the same thing across
two agents. Worse, the GenAI spec mandates **exact** histogram bucket boundaries and a
specific token-usage shape (one instrument filtered by `gen_ai.token.type`, not
separate instruments); get those wrong and your data is non-conformant and won't line
up with anyone else's collector views.

feat-005 ships both layers at once: the **SDK's product metrics** under the
`agentforge.*` namespace (FR-6 — the value-add) and the **OTel GenAI histograms** with
the spec's exact units and buckets (the standard). Both are *derived automatically*
from the run records the runtime already produces (feat-002) — the agent author emits
nothing extra.

## 2. Why this belongs in the SDK (vs each agent rolling its own)

- **What shipping it as the SDK makes possible:** every consuming agent emits the
  *same* instruments, with the *same* names, units, buckets, and attributes. A platform
  team writes one Grafana panel — `sum(rate(forgesight_agent_runs_total[5m]))`,
  `histogram_quantile(0.99, gen_ai_client_operation_duration_bucket)` — and it works for
  every agent in the org. Hand-rolled metrics make that impossible: each agent's `p99`
  query needs bespoke knowledge of that agent's bucket choices.
- **What the SDK ownership protects:**
  - **Spec-exactness.** The GenAI histograms have mandated buckets. The SDK encodes
    them once; an agent can't accidentally ship `[0.1, 0.5, 1, 5]` and silently break
    cross-agent comparison or collector recording rules.
  - **The `agentforge.*` vs `gen_ai.*` boundary.** The product metrics are the SDK's
    value-add and live under `agentforge.*`; the spec histograms live under `gen_ai.*`.
    Centralising the split means no agent muddies the namespaces — cost is
    `forgesight.usage.cost_usd` aggregated into `forgesight.agent.cost_total`, **never**
    a `gen_ai.*` metric (OTel defines none — ADR-0005).
  - **Derive-don't-emit.** Because metrics derive from the same `Record`s as spans,
    they can never disagree with the traces. An agent that emits metrics by hand
    inevitably double-counts or drifts from its own spans.
- **The anti-pattern if we don't:** N agents, N incompatible metric vocabularies, no
  fleet view, and non-conformant histograms that won't aggregate. Exactly the
  fragmentation the SDK exists to end (requirements §1.1).

## 3. How agents/teams consuming the SDK benefit

- **Before:** an agent author writes a `MeterProvider`, declares ~8 instruments, hooks
  each into the run loop, picks units/buckets, and hopes they match the spec — easily
  100+ lines, and wrong on the buckets.
  **After:** they write *nothing*. The runtime already records runs/calls (feat-002);
  feat-005 derives all metrics from those records. Turn on the metric reader via config.
- **Day-1 fleet dashboard.** `forgesight_agent_runs_total`,
  `forgesight_agent_failures_total`, `forgesight_agent_cost_total`,
  `forgesight_agent_duration_ms`, `forgesight_tool_invocations_total`,
  `forgesight_mcp_invocations_total` — every one tagged with agent name/version so a
  platform team slices by agent with zero per-agent setup.
- **Conformant GenAI latency/token panels for free.** `gen_ai.client.token.usage`
  (filtered by `gen_ai.token.type`), `gen_ai.client.operation.duration`,
  `gen_ai.workflow.duration` — exact buckets, so Honeycomb/Datadog/Prometheus recording
  rules built for the spec just work.
- **Push *or* pull, decided at deploy.** Same instruments feed a push
  `PeriodicExportingMetricReader` (OTLP/Datadog) or a pull `MetricReader`
  (Prometheus `/metrics`, feat-012) — a config choice, not a code change.
- **Cost rolls up automatically.** Per-call `forgesight.usage.cost_usd` (feat-006)
  aggregates into `forgesight.agent.cost_total` — FinOps gets chargeable numbers per
  agent/team without the agent author touching cost code.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — metrics derive from the runs you already instrument; you emit nothing extra
import forgesight

forgesight.configure()
# FORGESIGHT_METRICS_ENABLED=true
# FORGESIGHT_METRIC_EXPORT_INTERVAL_MILLIS=10000   # push reader cadence

from forgesight import telemetry

with telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5"):
        ...
    with run.tool_call(name="web_search"):
        ...
# on run exit the runtime records:
#   forgesight.agent.runs_total{agent.name=issue-classifier} += 1
#   forgesight.agent.cost_total += sum(llm cost)
#   forgesight.agent.duration_ms.record(elapsed)
#   gen_ai.client.token.usage.record(input,  {gen_ai.token.type=input,  provider=anthropic})
#   gen_ai.client.token.usage.record(output, {gen_ai.token.type=output, provider=anthropic})
#   gen_ai.client.operation.duration.record(llm_seconds, {gen_ai.operation.name=chat, ...})
#   forgesight.tool.invocations_total{tool.name=web_search} += 1
```

```python
# python — choose the reader (push vs pull) explicitly
from forgesight_core.metrics import MetricConfig

forgesight.configure(metrics=MetricConfig(
    enabled=True,
    export_interval_millis=10_000,         # push reader cadence
    enabled_instruments=None,              # None = all; or a subset (see §4.5)
))
```

```typescript
// typescript (parity sketch — targets 0.4)
import { configure } from '@agentforge/sdk';
configure({ metrics: { enabled: true, exportIntervalMillis: 10_000 } });
```

### 4.2 Public API / contract

```python
# forgesight_core/metrics/instruments.py
class InstrumentRegistry:                              # experimental — internals may move
    """Owns the SDK's MeterProvider-bound instruments. Created at configure();
    fed by the runtime's record stream. Agent code never touches it directly."""
    def __init__(self, meter: Meter, *, enabled: frozenset[str] | None = None) -> None: ...
    def record_run(self, run: AgentRun) -> None: ...           # → forgesight.agent.* + gen_ai.workflow.duration
    def record_llm_call(self, call: LLMCall) -> None: ...      # → gen_ai.client.* + agentforge cost rollup
    def record_tool_call(self, call: ToolCall) -> None: ...    # → forgesight.tool.invocations_total
    def record_mcp_call(self, call: MCPCall) -> None: ...      # → forgesight.mcp.invocations_total + mcp.client.operation.duration

# forgesight_core/metrics/config.py
@dataclass(slots=True)
class MetricConfig:                                    # stable
    enabled: bool = True
    export_interval_millis: int = 10_000              # PeriodicExportingMetricReader cadence
    enabled_instruments: frozenset[str] | None = None # None ⇒ all
```

**FR-6 product metrics — namespace `agentforge.*` (the SDK's value-add):**

| Instrument | Type | Unit | Key attributes |
|---|---|---|---|
| `forgesight.agent.runs_total` | Counter | `{run}` | `agent.name`, `agent.version`, `status` |
| `forgesight.agent.failures_total` | Counter | `{run}` | `agent.name`, `agent.version`, `error.type` |
| `forgesight.agent.cost_total` | Counter | `usd` | `agent.name`, `gen_ai.provider.name` |
| `forgesight.agent.duration_ms` | Histogram | `ms` | `agent.name`, `status` |
| `forgesight.tool.invocations_total` | Counter | `{invocation}` | `gen_ai.tool.name`, `gen_ai.tool.type`, `status` |
| `forgesight.mcp.invocations_total` | Counter | `{invocation}` | `mcp.method.name`, `status` |

> Identifier note: the FR-6 names (`agent_runs_total`, …) map onto these dotted OTel
> names; Prometheus rendering flattens dots to underscores
> (`forgesight_agent_runs_total`). The dotted form is canonical (P4).

**OTel GenAI histograms — namespace `gen_ai.*` (the spec; exact units + buckets from
[`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.4):**

| Instrument | Type | Unit | Buckets |
|---|---|---|---|
| `gen_ai.client.token.usage` | Histogram | `{token}` | `[1,4,16,64,256,1024,4096,16384,65536,262144,1048576,4194304,16777216,67108864]` |
| `gen_ai.client.operation.duration` | Histogram | `s` | `[0.01,0.02,0.04,0.08,0.16,0.32,0.64,1.28,2.56,5.12,10.24,20.48,40.96,81.92]` |
| `gen_ai.client.operation.time_to_first_chunk` | Histogram | `s` | as duration |
| `gen_ai.workflow.duration` | Histogram | `s` | `[1,5,10,30,60,120,300,600,1800,3600,7200]` |
| `mcp.client.operation.duration` | Histogram | `s` | as duration |

`gen_ai.client.token.usage` is **one instrument filtered by `gen_ai.token.type`**
(`input` / `output` / `cache_read` / `cache_creation` / `reasoning`), never split into
per-type instruments. Required attrs: token usage → `gen_ai.operation.name`,
`gen_ai.provider.name`, `gen_ai.token.type`; duration → `gen_ai.operation.name`,
`gen_ai.provider.name` (+ `error.type` on error). **Billing rule:** report billed
tokens when both billed and consumed counts exist.

### 4.3 Internal mechanics

**Derive, don't double-emit.** The runtime (feat-002) already produces a `Record` when
each run/call ends. The `InstrumentRegistry` subscribes to that same record stream and
records metric points — so metrics and spans are guaranteed consistent (they come from
one source). No separate instrumentation path; the agent author adds nothing.

```
run/call ends (feat-002 hot path)
   │  immutable Record built (also goes to the trace pipeline, feat-003)
   ▼
InstrumentRegistry.record_<kind>(record)        # synchronous, in-memory, O(1)
   ├── agentforge.* product metrics (counters/histograms)
   └── gen_ai.* spec histograms (token.type-tagged; exact buckets)
   ▼
MetricReader (push or pull)                      # OTel reader model — §4.7
   ├── PeriodicExportingMetricReader → OTLP/Datadog        (push; export_interval_millis)
   └── MetricReader (pull)            → Prometheus /metrics (feat-012)
```

**Reader model (OTel).** The SDK binds instruments to a `MeterProvider`; *how* they
leave the process is the reader's job — push via `PeriodicExportingMetricReader`
(default cadence `export_interval_millis`, 10 s) or pull via a `MetricReader` scraped on
demand (Prometheus). Both are fault-isolated and bounded
([`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.7); a stuck
metric backend never blocks the agent (P6/NFR-2). Recording a metric point is in-memory
aggregation — no network on the hot path.

**`agentforge.*` vs `gen_ai.*` separation.** Two clean families. `gen_ai.*` is exactly
the spec (we don't add to it). `agentforge.*` is everything the spec doesn't cover —
run/failure counts, the cost rollup, agent-level duration. Cost is
`forgesight.agent.cost_total`, summing the per-call `forgesight.usage.cost_usd`
(feat-006); it is **never** a `gen_ai.*` metric.

### 4.4 Module packaging

- Lives in **`forgesight-core`** (always installed with the runtime) — metrics are
  core value, not an optional backend. Depends on `forgesight-api` +
  `opentelemetry-api` (the API, not vendor SDKs — P1).
- No separate install; available wherever `forgesight` (or `-core`) is.

  ```yaml
  # forgesight.yaml
  metrics:
    enabled: true
    export_interval_millis: 10000
    enabled_instruments: null        # null = all; or a list to select a subset
  ```

- **Entry point (for the reader, when a backend supplies one):** pull readers ship in
  integration packages (e.g. Prometheus, feat-012) and register under
  `forgesight.metric_readers`; the push OTLP reader is configured by
  `forgesight-otel` (feat-004). The instruments themselves are core and need no
  entry point.

### 4.5 Configuration

| Key (YAML under `metrics:`) | Env | Default | Validation |
|---|---|---|---|
| `enabled` | `FORGESIGHT_METRICS_ENABLED` | `true` | bool |
| `export_interval_millis` | `FORGESIGHT_METRIC_EXPORT_INTERVAL_MILLIS` | `10000` | int > 0 (push reader cadence) |
| `enabled_instruments` | `FORGESIGHT_ENABLED_INSTRUMENTS` | `null` (all) | comma-list of known instrument names; unknown name → fail-fast at `configure()` |

`enabled_instruments` selects a subset (e.g. drop `gen_ai.workflow.duration` if you have
no workflows). Constructor `MetricConfig` overrides env, which overrides YAML
(last-wins; feat-010). The OTLP/Prometheus reader endpoints are configured in their own
integration packages (feat-004 / feat-012); this feature owns only the instrument
inventory + the reader cadence/selection knobs (P8 — every knob named + defaulted).

## 5. Plug-and-play & upgrade story

In `forgesight-core` — always installed; nothing to add at scaffold time. Turning
metrics on/off and selecting instruments is config. Adding a *backend* for the metrics
(Prometheus pull, OTLP push) is a `pip install` of feat-012/feat-004 + one config line,
no agent code change (P2). Upgrade safety: the instrument inventory + units + buckets
are part of the stable mapping (versioned with `semconv_version` for the `gen_ai.*`
family); adding a new product instrument is a minor bump, renaming one is a major bump
with an ADR (P5).

## 6. Cross-language parity

Identical across Python / TypeScript: every instrument name, type, unit, bucket-boundary
list, the `gen_ai.token.type` filtering, the `agentforge.*` vs `gen_ai.*` split, and the
push/pull reader model. Allowed to differ: the OTel SDK object names (`MeterProvider` /
`PeriodicExportingMetricReader` vs the JS equivalents) and idiomatic config naming.
Python first (0.1); TS by 0.4 (architecture §10).

## 7. Test strategy

- **Unit:** each `record_<kind>` produces exactly the expected metric points with the
  right attributes; `forgesight.agent.cost_total` equals the sum of per-call
  `forgesight.usage.cost_usd`; failures increment `forgesight.agent.failures_total` with
  `error.type`.
- **Bucket conformance:** assert the GenAI histograms register the **exact** spec bucket
  boundaries and units (`{token}`, `s`); `gen_ai.client.token.usage` is one instrument
  filtered by `gen_ai.token.type`, not several.
- **Derive-consistency:** for a recorded run, the metric counts agree with the spans the
  same records produced (no double-count, no drift).
- **Reader model:** push via OTel `InMemoryMetricReader` snapshot; a pull reader returns
  the same aggregation on scrape (feat-012 reuses this).
- **Fault isolation (P6):** a wedged metric exporter never blocks the run; recording is
  in-memory and non-blocking (NFR-2).
- **Selection:** `enabled_instruments` subset emits only the named instruments; an
  unknown name fails fast at `configure()`.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Buckets drift from the spec | Boundaries encoded once in core; conformance test asserts them; versioned with `semconv_version`. |
| `agentforge.*` and `gen_ai.*` blur | Hard namespace split enforced in code + review; cost is `agentforge.*` only (ADR-0005). |
| High cardinality (per-model/per-tool attrs) | Bounded attribute set documented; `model` on histograms only where spec-required; business metadata stays on spans, not metrics. |
| Billed vs consumed token counts disagree | Spec billing rule: report billed when both exist; flagged in the record. |
| Push vs pull default | Default is push (`PeriodicExportingMetricReader`, 10 s); pull is opt-in via feat-012. |

## 9. Out of scope

- **The OTLP/Prometheus metric *transport*** — push reader wiring is feat-004, pull
  `/metrics` + push-gateway is feat-012. This feature owns the instruments + the OTel
  reader model, not the backend endpoints.
- **Cost computation** — feat-006 computes `forgesight.usage.cost_usd`; this feature only
  aggregates it into `forgesight.agent.cost_total`.
- **Defining new `gen_ai.*` metrics.** We emit exactly the spec set; new SDK metrics go
  under `agentforge.*` (P4).
- **Alerting / recording rules / SLOs** — configured in the user's existing stack
  (requirements §11); we emit, they alert.

## 10. References

- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.4 — exact instruments, units, buckets, `gen_ai.token.type` filtering
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.7 — metrics-vs-traces reader split
- [`../design/architecture.md`](../design/architecture.md) §4 (model), §9 (perf)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P4, P6, P8
- [`../adr/0001-opentelemetry-first-canonical-model.md`](../adr/0001-opentelemetry-first-canonical-model.md), [`../adr/0005-cost-as-namespaced-extension.md`](../adr/0005-cost-as-namespaced-extension.md)
- feat-002 (runtime — record source), feat-004 (OTLP metric export), feat-006 (cost), feat-012 (Prometheus pull)
- Requirements FR-6

---

## Implementation status

**Status: shipped (Python).** Landed via PR #5 (CI green on Python 3.11/3.12/3.13) in
`forgesight-core` (`forgesight_core.metrics`). 124 tests workspace-wide, **97.5%
coverage**, `mypy --strict` + `ruff` clean.

| Module | Scope |
|---|---|
| `metrics/config.py` | `MetricConfig` (`enabled` / `export_interval_millis` / `enabled_instruments`) + validation. |
| `metrics/instruments.py` | `MetricsSubsystem`: a local OTel `MeterProvider` with the GenAI histograms' **exact** spec buckets (via `View` + `ExplicitBucketHistogramAggregation`), the six `forgesight.*` product instruments, `record(Record)` derivation, and a default `InMemoryMetricReader`. Bucket constants + `KNOWN_INSTRUMENTS`. |
| wiring | `Runtime.metrics`; `emit_record` calls `metrics.record(record)` **before sampling** (metrics count all runs); `configure(metrics=…, metric_reader=…)`; `shutdown()` shuts the provider down. |

Both metric families ship: `forgesight.*` product metrics (runs / failures / cost /
duration / tool & mcp invocations) and the `gen_ai.*` histograms (token usage filtered
by `gen_ai.token.type`, operation/workflow/mcp durations) with the mandated buckets.
`opentelemetry-sdk` is now a `forgesight-core` dependency (vendor-neutral, P4).

### Deviations from this spec

- **`forgesight.*` namespace** throughout (the spec prose's `agentforge.*` was a
  pre-rename leftover; the instrument table already used `forgesight.*`).
- **`record(Record)` dispatch** instead of the spec's per-submodel
  `record_run/record_llm_call/…` signatures — the `Record` carries the run/status
  context the submodels lack, and keeps one call site in the runtime.
- **`forgesight.agent.cost_total` is keyed by `gen_ai.provider.name` only** (not
  `agent.name`) — an LLM `Record` doesn't carry the agent name; agent-level cost
  attribution is available on spans via business metadata (feat-022 closes the loop).
- **A local (non-global) `MeterProvider`** is built per `configure()` — avoids OTel's
  "overriding global MeterProvider" warning and keeps runtimes isolated; default
  reader is `InMemoryMetricReader` (collectable via `runtime.metrics.collect()`).
- **Transport deferred to integrations** — push OTLP (feat-004 follow-up) / pull
  Prometheus (feat-012) inject a `MetricReader`; this feature owns the instruments +
  the default in-memory reader (spec §9).

### Not yet implemented

Push `PeriodicExportingMetricReader` wiring in `forgesight-otel`; Prometheus pull
reader (feat-012); agent-name on `cost_total`; TypeScript port.

## Runbook

### How do I get fleet metrics? (I emit nothing extra)

Metrics derive from the runs you already instrument — just keep them enabled (default):

```python
import forgesight
forgesight.configure()   # metrics on by default
# now every run records forgesight.agent.runs_total / .cost_total / .duration_ms,
# gen_ai.client.token.usage (by token type), gen_ai.client.operation.duration, etc.
```

### How do I inspect metrics in a test?

```python
from forgesight import configure, get_runtime, telemetry
configure(sync_export=True)
with telemetry.agent_run("c") as run, run.llm_call("anthropic", "m") as call:
    call.record_usage(input=100, output=50)
data = get_runtime().metrics.collect()   # OTel MetricsData from the in-memory reader
```

### How do I select a subset of instruments, or turn metrics off?

```python
from forgesight import configure
from forgesight_core import MetricConfig

configure(metrics=MetricConfig(enabled_instruments=frozenset({"forgesight.agent.runs_total"})))
configure(metrics=MetricConfig(enabled=False))   # off entirely
```

An unknown instrument name fails fast at `configure()`.

### Where does cost show up in metrics?

`forgesight.agent.cost_total` (USD counter), summed from each LLM call's
`forgesight.usage.cost_usd` (feat-006). It is never a `gen_ai.*` metric (OTel defines
no cost — ADR-0005).
