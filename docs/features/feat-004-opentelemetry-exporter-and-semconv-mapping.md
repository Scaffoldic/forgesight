# feat-004: OpenTelemetry exporter & GenAI semantic-convention mapping

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-004 |
| **Title** | OpenTelemetry exporter & GenAI semantic-convention mapping (OTLP traces + metrics; W3C propagation) |
| **Status** | `shipped` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-otel` |
| **Depends on** | feat-001, feat-002, feat-003 |
| **Blocks** | feat-013 (Langfuse), feat-015 (Datadog) |

---

## 1. Why this feature

This is the **keystone exporter**. Everything the SDK records — runs, LLM calls, tool
calls, MCP calls, cost, metrics — only becomes useful when it lands in a backend an
operator can actually look at. feat-001/002/003 produce immutable `Record`s and fan
them through a bounded pipeline; without feat-004 they fan out to nothing.

The pain it removes is concrete. An SRE picks up an incident — "run `01HX…` cost $4.10
and never finished." Today, answering that means: stand up a tracer provider, learn the
GenAI semantic conventions, hand-map every token field to the right `gen_ai.*`
attribute, decide what to do with cost (which OTel doesn't define), get span
names/kinds right, wire W3C propagation so the A2A hop doesn't break the trace, and
keep all of it in sync as the conventions churn. That is days of work per team, and it
rots the moment the spec moves — which it does, because the GenAI conventions are
**all at `Development` stability with no tagged release**.

feat-004 ships that mapping once, correctly, pinned to a known commit, and versioned.
One `pip install forgesight-otel` + one config line turns every recorded run into a
standards-compliant OTLP span tree plus the GenAI metric instruments — and because the
wire format is OTLP, that one package unlocks **Datadog, Honeycomb, Jaeger, Grafana
Tempo, SigNoz, New Relic, and Arize Phoenix with no additional package** (P4).

## 2. Why this belongs in the SDK (vs each agent rolling its own)

- **What shipping it as the SDK makes possible:** a *single* deterministic
  domain-model → OTLP mapping that every consuming agent shares. Two agents from two
  teams emit byte-identical span names, attribute keys, and metric buckets, so their
  telemetry is directly comparable in one dashboard. An agent author who hand-rolls
  this gets a mapping that is correct for exactly their backend and drifts from
  everyone else's.
- **What the SDK ownership protects:**
  - **Spec-churn insulation.** The conventions are pre-release and moving. The mapping
    lives in *one* module, pinned to a commit, stamped with `semconv_version`. When
    upstream renames an attribute, we re-pin here and bump the version; **callers see
    nothing** (P5). An agent that inlined `gen_ai.usage.input_tokens` breaks on the
    next rename.
  - **The cost convention.** OTel defines no cost attribute. Left to each agent, half
    would squat on `gen_ai.usage.cost` (a future clash waiting to happen) and half
    would invent their own key. The SDK emits cost as the namespaced extension
    `forgesight.usage.cost_usd` — **never** a `gen_ai.*` identifier the spec hasn't
    shipped (ADR-0005).
  - **Secure-by-default content gating.** Prompt/completion/argument content is
    Opt-In, off by default (P7). Centralising the gate means no agent accidentally
    ships PII to a collector because someone forgot the flag.
- **The anti-pattern if we don't:** N teams, N subtly-different mappings, N copies of a
  cost calc, N broken traces the first time the spec moves. Exactly the fragmentation
  the SDK exists to end (requirements §1.1).

If this could live in a derived agent, it would — but the whole value is *uniformity
across agents* and *one place to re-pin*. That is framework-shaped by definition.

## 3. How agents/teams consuming the SDK benefit

- **Before:** an agent author writes ~200–400 lines of tracer-provider setup, attribute
  mapping, cost handling, and propagation glue — then re-tests it every time the GenAI
  conventions move.
  **After:** `pip install forgesight-otel` and set `FORGESIGHT_OTEL_ENDPOINT`.
  Zero mapping code. The span tree, token attributes, cost, and metrics appear in their
  collector.
- **Defer the backend decision.** A team can develop against the console/in-memory
  exporter, then choose Datadog vs Honeycomb vs Tempo at deploy time — a config change,
  not a code change. The OTLP path reaches all of them with this one package.
- **One trace across an A2A hop, for free.** W3C TraceContext is injected and extracted
  by the SDK, so a run that calls a peer agent produces one end-to-end trace — no manual
  header threading.
- **Comparable fleets.** Because the mapping is identical across every consumer, a
  platform team can build one dashboard ("cost per agent", "p99 op duration by
  provider") that works for every agent in the org without per-agent customisation.
- **Upgrade safety.** When upstream cuts a real GenAI release, the team upgrades
  `forgesight-otel` and gets the new mapping behind `semconv_version` with a
  one-minor back-compat flag — their agent code is untouched.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — zero-code: configure once, the OTel exporter auto-loads via entry point
import forgesight

forgesight.configure()   # reads FORGESIGHT_* env + forgesight.yaml
# FORGESIGHT_EXPORTERS=otel
# FORGESIGHT_OTEL_ENDPOINT=http://otel-collector:4317

from forgesight import telemetry

with telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        ...  # SDK records tokens/cost; exporter maps → OTLP span + metrics
```

```python
# python — explicit construction (full control)
from forgesight_otel import OTelExporter

exporter = OTelExporter(
    endpoint="http://otel-collector:4317",
    protocol="grpc",                 # "grpc" | "http/protobuf"
    service_name="issue-classifier",
    sample_rate=1.0,
    capture_content=False,           # P7: prompts/completions OFF by default
    emit_legacy_system=False,        # gen_ai.system back-compat OFF by default
    headers={"x-honeycomb-team": "${HONEYCOMB_API_KEY}"},
)
forgesight.configure(exporters=[exporter])
```

```typescript
// typescript (parity sketch — targets 0.4)
import { configure } from '@agentforge/sdk';
import { OTelExporter } from '@agentforge/sdk-otel';

configure({
  exporters: [new OTelExporter({
    endpoint: 'http://otel-collector:4317',
    protocol: 'grpc',
    serviceName: 'issue-classifier',
    sampleRate: 1.0,
    captureContent: false,
    emitLegacySystem: false,
  })],
});
```

Nothing about the agent's instrumentation calls changes between backends. The exporter
is the only thing that knows about OTLP; swapping to Datadog is a config edit.

### 4.2 Public API / contract

```python
# forgesight_otel/exporter.py
class OTelExporter:                                  # stable
    """A TelemetryExporter that maps Records → OTLP spans + GenAI metrics.

    Implements the forgesight_api.TelemetryExporter Protocol (feat-001).
    Constructed by the user or auto-loaded via the `forgesight.exporters`
    entry point under the name "otel".
    """
    def __init__(
        self,
        *,
        endpoint: str | None = None,                 # OTLP endpoint; None → OTel env defaults
        protocol: str = "grpc",                      # "grpc" | "http/protobuf"
        service_name: str = "agentforge-agent",
        sample_rate: float = 1.0,                    # head-based TraceIdRatioBased
        capture_content: bool = False,               # P7 gate for gen_ai.*.messages
        emit_legacy_system: bool = False,            # also emit gen_ai.system
        headers: dict[str, str] | None = None,       # OTLP headers (auth)
        resource_attributes: dict[str, str] | None = None,
    ) -> None: ...

    # --- TelemetryExporter Protocol (locked in feat-001) ---
    def export(self, records: Sequence[Record]) -> ExportResult: ...   # never raises (P6)
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...

# forgesight_otel/semconv.py
SEMCONV_VERSION: str = "genai-dev-<pinned-commit-short-sha>"   # stable: stamped on Resource
SEMCONV_COMMIT: str  = "open-telemetry/semantic-conventions-genai@<full-sha>"

class SemConvMapper:                                 # experimental — internals may move
    """Pure mapping: Record → (span name, SpanKind, attributes, events) and
    Record → metric points. The single source of truth for the OTLP wire format.
    Re-pinning the spec changes only this module (P5)."""
    def span_name(self, record: Record) -> str: ...
    def span_kind(self, record: Record) -> SpanKind: ...
    def attributes(self, record: Record) -> dict[str, AttributeValue]: ...
    def metric_points(self, record: Record) -> Sequence[MetricPoint]: ...
```

**Span mapping** (from [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2). Span name = `{operation.name} {primary identifier}`:

| Domain type | operation.name | Span name | Span kind |
|---|---|---|---|
| WorkflowRun | `invoke_workflow` | `invoke_workflow {workflow.name}` | INTERNAL |
| AgentRun (local) | `invoke_agent` | `invoke_agent {agent.name}` | INTERNAL |
| AgentRun (remote/hosted) | `invoke_agent` | `invoke_agent {agent.name}` | CLIENT |
| AgentRun create (hosted) | `create_agent` | `create_agent {agent.name}` | CLIENT |
| Step | `plan` / *(custom)* | `plan {agent.name}` / `{step.name}` | INTERNAL |
| LLMCall chat | `chat` | `chat {request.model}` | CLIENT |
| LLMCall completion | `text_completion` | `text_completion {request.model}` | CLIENT |
| LLMCall embeddings | `embeddings` | `embeddings {request.model}` | CLIENT |
| ToolCall | `execute_tool` | `execute_tool {tool.name}` | INTERNAL |
| MCPCall (tools/call) | `execute_tool` | `tools/call {tool.name}` | CLIENT |
| MCPCall (other) | *(unset)* | `{mcp.method.name}` | CLIENT |

**Attribute mapping** (locked to the design doc; exporter emits exactly these keys):

- **Identity/routing (all spans):** `gen_ai.agent.name`, `gen_ai.agent.version`,
  `gen_ai.conversation.id` (only when a real session id exists — never fabricated),
  **`gen_ai.provider.name`** (canonical discriminator), business `metadata.*` as
  namespaced span attributes (FR-5), `error.type` + span status on failure (FR-7).
  `run_id` (ULID) rides as `forgesight.run.id` (extension) — *not* `gen_ai.agent.id`,
  which is reserved for stable hosted ids.
- **LLMCall:** `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`,
  `gen_ai.usage.input_tokens` (incl. cached, per spec), `gen_ai.usage.output_tokens`,
  `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`,
  `gen_ai.usage.reasoning.output_tokens`, `gen_ai.response.finish_reasons`,
  `gen_ai.request.temperature` / `…max_tokens` / `…top_p` / `…top_k` / …,
  `gen_ai.response.time_to_first_chunk`, and **`forgesight.usage.cost_usd`**
  (extension — OTel defines no cost attr; ADR-0005).
- **ToolCall:** `gen_ai.tool.name`, `gen_ai.tool.type` (`function`/`extension`/
  `datastore`), `gen_ai.tool.call.id`, `gen_ai.tool.description`. Args/results
  (`gen_ai.tool.call.arguments` / `…result`) are **Opt-In**, gated by `capture_content`.
- **MCPCall:** `mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`,
  `mcp.resource.uri`; plus `gen_ai.tool.name` and `gen_ai.operation.name = execute_tool`
  on `tools/call`; `error.type = tool_error` when `CallToolResult.isError`. A
  `tools/call` is **not** double-instrumented with a separate `execute_tool` span.
- **Content (Opt-In, P7):** `gen_ai.input.messages`, `gen_ai.output.messages`,
  `gen_ai.system_instructions` as JSON strings — emitted **only** when
  `capture_content` is on; off by default.

**Metric mapping** — instruments, units, and buckets are emitted exactly as below; the
instrument inventory + derivation lives in feat-005, but feat-004 owns the OTLP metric
exporter wiring:

| Metric | Instrument | Type | Unit | Buckets |
|---|---|---|---|---|
| token usage | `gen_ai.client.token.usage` | Histogram | `{token}` | `[1,4,16,64,256,1024,4096,16384,65536,262144,1048576,4194304,16777216,67108864]` |
| op duration | `gen_ai.client.operation.duration` | Histogram | `s` | `[0.01,0.02,0.04,0.08,0.16,0.32,0.64,1.28,2.56,5.12,10.24,20.48,40.96,81.92]` |
| TTFT | `gen_ai.client.operation.time_to_first_chunk` | Histogram | `s` | as duration |
| workflow duration | `gen_ai.workflow.duration` | Histogram | `s` | `[1,5,10,30,60,120,300,600,1800,3600,7200]` |
| MCP op duration | `mcp.client.operation.duration` | Histogram | `s` | as duration |

`gen_ai.client.token.usage` is **filtered by `gen_ai.token.type`** (not split into
separate instruments). Cost is **never** emitted as a `gen_ai.*` metric — it is the
SDK's own `forgesight.usage.cost_usd` (feat-005 / feat-006).

### 4.3 Internal mechanics

The exporter is a pipeline-side `TelemetryExporter` (feat-003) — it runs on the export
worker, never the hot path. On construction it builds (idempotently, respecting any
existing user-installed provider):

```
OTelExporter.__init__
   ├── TracerProvider(resource = Resource({
   │       service.name, forgesight.semconv_version=SEMCONV_VERSION, …resource_attributes }))
   │       + sampler = TraceIdRatioBased(sample_rate)         ← head-based (otel mapping §4.5)
   │       + BatchSpanProcessor(OTLPSpanExporter(endpoint, protocol, headers))
   └── MeterProvider(resource) + PeriodicExportingMetricReader(OTLPMetricExporter(...))

OTelExporter.export(records)   # on the worker
   for record in records:
       span = mapper → start_span(name, kind, parent=record.parent_ctx)
       span.set_attributes(mapper.attributes(record))     # cost as forgesight.usage.cost_usd
       if capture_content: span.set_attributes(content_attrs)   # P7 gate
       record metric_points → instruments (token.type-tagged histograms)
       span.set_status(OK | ERROR + error.type)
       span.end(record.end_unix_nanos)
   return ExportResult.SUCCESS        # never raises (P6); on OTLP failure → FAILURE
```

**Why the SDK already produces parent context:** the span tree is built in feat-002
against the domain model; each `Record` carries its parent linkage, so the exporter
reconstructs the tree without owning run state.

**W3C TraceContext propagation.** On an outbound A2A/MCP hop the SDK injects
`traceparent` + `tracestate` via `TraceContextTextMapPropagator().inject()` (no-op when
no active span); the callee extracts the same and opens its `invoke_agent` /
`tools/call` span as a child — one end-to-end trace across processes. `run_id` rides as
baggage for log correlation.

**Pinning & versioning.** The mapping is pinned to a specific
`semantic-conventions-genai` commit (`SEMCONV_COMMIT`) and stamped on every span's
Resource as `forgesight.semconv_version` (`SEMCONV_VERSION`). Re-pinning is a feat-004
change only; the previous mapping stays behind a flag for one minor (otel mapping §6).

### 4.4 Module packaging

- Lives in the opt-in integration package **`forgesight-otel`** (one backend
  family per package — P2). Depends on `forgesight-core`, the OTel **SDK**
  (`opentelemetry-sdk`), and the OTLP exporters
  (`opentelemetry-exporter-otlp-proto-grpc` / `…-http`). Core never depends on these.
- Install + enable:

  ```bash
  pip install forgesight-otel
  ```

  ```yaml
  # forgesight.yaml
  exporters:
    - name: otel
      config:
        endpoint: "http://otel-collector:4317"
        protocol: "grpc"
        service_name: "issue-classifier"
        sample_rate: 1.0
        capture_content: false
        emit_legacy_system: false
        headers:
          x-honeycomb-team: "${HONEYCOMB_API_KEY}"
  ```

- **Entry-point registration** (auto-load by name from config):

  ```toml
  # forgesight-otel / pyproject.toml
  [project.entry-points."forgesight.exporters"]
  otel = "forgesight_otel.exporter:OTelExporter"
  ```

  Resolved by `configure()` (feat-010) exactly like any third-party exporter.

### 4.5 Configuration

| Key (YAML under `exporters[].config`) | Env | Default | Validation |
|---|---|---|---|
| `endpoint` | `FORGESIGHT_OTEL_ENDPOINT` | OTel env default (`OTEL_EXPORTER_OTLP_ENDPOINT`) | URL; required in prod |
| `protocol` | `FORGESIGHT_OTEL_PROTOCOL` | `grpc` | one of `grpc`, `http/protobuf` |
| `service_name` | `FORGESIGHT_OTEL_SERVICE_NAME` | `agentforge-agent` | non-empty |
| `sample_rate` | `FORGESIGHT_SAMPLE_RATE` | `1.0` | `0.0 ≤ x ≤ 1.0` |
| `capture_content` | `FORGESIGHT_CAPTURE_CONTENT` | `false` | bool (P7 — off by default) |
| `emit_legacy_system` | `FORGESIGHT_OTEL_EMIT_LEGACY_SYSTEM` | `false` | bool |
| `headers` | `FORGESIGHT_OTEL_HEADERS` | `{}` | `k=v,k=v` map; `${VAR}` expansion |
| `resource_attributes` | `OTEL_RESOURCE_ATTRIBUTES` | `{}` | `k=v` map |

Constructor kwargs override env, which override YAML (last-wins; feat-010). Batch/queue
knobs (`max_queue_size`, `max_export_batch_size`, `schedule_delay_millis`,
`export_timeout_millis`) come from the shared pipeline config
([`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.8).

## 5. Plug-and-play & upgrade story

Add later with `pip install forgesight-otel` + one `exporters:` block — no agent
code change (P2). It coexists with other exporters (Langfuse, Prometheus, custom) under
the same `exporters` list; the pipeline fans out to all (FR-11). Upgrading the package
re-pins the semconv mapping behind `forgesight.semconv_version`; the previous mapping
stays available for one minor via a flag, so a backend mid-migration isn't broken
(P5). The exporter satisfies the locked `TelemetryExporter` Protocol, so a `-core`
minor bump never breaks it.

## 6. Cross-language parity

Identical across Python / TypeScript: span names, span kinds, every attribute key, the
cost extension `forgesight.usage.cost_usd`, the metric instruments + units + buckets,
the content-capture gate, and `semconv_version`. Allowed to differ: the OTel SDK object
names (`TracerProvider`/`BatchSpanProcessor` vs the JS equivalents), `grpc` vs
`http/protobuf` defaults per ecosystem, idiomatic config naming. Python lands first
(0.1); TS targets the same surface by 0.4 (architecture §10).

## 7. Test strategy

- **Unit:** `SemConvMapper` table-driven — each domain type → exact span name, kind,
  and attribute set; cost lands on `forgesight.usage.cost_usd` and **never** on a
  `gen_ai.*` key; legacy `gen_ai.system` appears only when `emit_legacy_system`.
- **Content gating:** with `capture_content=False`, no `gen_ai.input.messages` /
  `…output.messages` / `…system_instructions` / `gen_ai.tool.call.arguments` on any
  span; with it on, they appear as JSON strings (P7).
- **Integration:** export against OTel's `InMemorySpanExporter` / `InMemoryMetricReader`
  and snapshot the full span tree + metric points (buckets, `gen_ai.token.type`
  filtering).
- **Propagation:** inject + extract `traceparent`/`tracestate`; assert cross-process
  trace-id stitching so an A2A hop is one trace.
- **Fault isolation (P6):** a wedged/erroring OTLP endpoint → `export()` returns
  `FAILURE`, never raises, never blocks the agent.
- **Conformance:** runs the feat-011 `TelemetryExporter` conformance suite.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| GenAI conventions churn (all `Development`, no release) | Single mapping module; pinned commit; `semconv_version` stamped; one-minor back-compat flag (otel mapping §6). |
| Backend still reads legacy `gen_ai.system` | `emit_legacy_system` opt-in emits both. |
| Content leaks PII into a collector | `capture_content` off by default (P7); redaction interceptor (feat-008) runs before export. |
| Cost attribute clashes with a future `gen_ai.usage.cost` | We namespace `forgesight.usage.cost_usd` and never squat `gen_ai.*` (ADR-0005). |
| Content as span attributes vs the `gen_ai.…inference.operation.details` event | Leaning span attributes primary, event behind a flag (otel mapping §8). |
| Map `Step` → `plan` always or only for plan phases? | Custom step name as INTERNAL span; `plan` only when semantically a plan (otel mapping §8). |

## 9. Out of scope

- **Vendor-native value-add** (Langfuse observation/cost mapping, Datadog DD-trace
  intake, Prometheus pull `/metrics`) — those are feat-012/013/015, which *derive* from
  this OTLP mapping.
- **Tail-based sampling** — a collector concern; we do head-based `TraceIdRatioBased`
  ([`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §3).
- **Defining new GenAI conventions.** We layer on the spec; cost is the one sanctioned
  extension (P4, ADR-0005). No OpenInference-style parallel `llm.*` set.
- **Authoring the metric instruments themselves** — instrument inventory + derivation
  is feat-005; feat-004 only wires the OTLP metric exporter.

## 10. References

- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) — the canonical mapping (this feature implements it)
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) — async export the exporter plugs into
- [`../design/architecture.md`](../design/architecture.md) §2 (keystone exporter), §4 (contract)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P4, P5, P6, P7
- [`../adr/0001-opentelemetry-first-canonical-model.md`](../adr/0001-opentelemetry-first-canonical-model.md), [`../adr/0004-pin-and-isolate-genai-semconv.md`](../adr/0004-pin-and-isolate-genai-semconv.md), [`../adr/0005-cost-as-namespaced-extension.md`](../adr/0005-cost-as-namespaced-extension.md), [`../adr/0007-content-capture-opt-in.md`](../adr/0007-content-capture-opt-in.md)
- feat-001 (domain model + SPIs), feat-002 (runtime), feat-003 (pipeline); feat-005 (metrics), feat-006 (cost) for the values mapped here
- OpenTelemetry GenAI semconv: <https://github.com/open-telemetry/semantic-conventions-genai>

---

## Implementation status

**Status: shipped (Python).** Landed via PR #4 (CI green on Python 3.11/3.12/3.13).
New package `forgesight-otel`. 115 tests workspace-wide, **97.7% coverage**,
`mypy --strict` + `ruff` clean.

| Module | Scope |
|---|---|
| `forgesight_otel/semconv.py` | `SemConvMapper` (pure `Record` → span name / `SpanKind` / attributes) + all attribute-key constants + `SEMCONV_VERSION`/`SEMCONV_COMMIT` pinning. Cost → `forgesight.usage.cost_usd`; `gen_ai.provider.name` canonical; legacy `gen_ai.system` opt-in; content gated; `error.type` on failure. |
| `forgesight_otel/exporter.py` | `OTelExporter` (`TelemetryExporter`): builds OTel `ReadableSpan`s carrying ForgeSight's own trace/span ids → OTLP. Injectable `span_exporter` for tests; lazy OTLP build for prod; `export` never raises (P6). |
| `forgesight_otel/propagation.py` | W3C TraceContext `inject` / `extract` helpers for A2A/MCP hops. |
| packaging | Entry point `forgesight.exporters → otel`; deps = `forgesight-core` + `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`; **never a dep of core** (P1). |

### Deviations from this spec

- **Metrics deferred to feat-005.** Spec §9 already puts the instrument inventory in
  feat-005; the OTLP metric-exporter wiring is moved there too so instruments + their
  export land together. feat-004 ships the **span** path (the keystone) +
  the semconv mapping + propagation.
- **Default protocol `http/protobuf`** (not the spec's `grpc`) — avoids a hard
  `grpcio` dependency (NFR-6 footprint). gRPC is the `forgesight-otel[grpc]` extra +
  `protocol="grpc"`.
- **`SEMCONV_COMMIT` is a placeholder pin** (`…@main` / `genai-dev-2026-06`) until a
  concrete sha is chosen; the mapping is still isolated + version-stamped (ADR-0004).
- **`error.type` is the `RunStatus` value** for now (e.g. `error`, `budget_exceeded`);
  exception-type detail comes from feat-009.
- **`SemConvMapper.metric_points` not implemented** (metrics → feat-005).

### Not yet implemented

OTLP metric exporter + GenAI metric instruments (feat-005); concrete semconv commit
pin; the experimental `gen_ai.…inference.operation.details` event form; TypeScript port.

## Runbook

### How do I ship telemetry to my collector / a vendor?

```bash
pip install forgesight-otel
```

```python
import forgesight
from forgesight_otel import OTelExporter

forgesight.configure(exporters=[OTelExporter(endpoint="http://otel-collector:4318")])
```

Any OTLP backend works through this one package — Datadog, Honeycomb, Jaeger, Grafana
Tempo, SigNoz, New Relic, Arize Phoenix. Point `endpoint=` at the backend; swapping
backends is a config change, not a code change.

### How do I use gRPC instead of HTTP?

```bash
pip install "forgesight-otel[grpc]"
```

```python
OTelExporter(endpoint="http://otel-collector:4317", protocol="grpc")
```

### Where does cost show up?

On the LLM span as **`forgesight.usage.cost_usd`** (OTel defines no cost attribute, so
it's a namespaced extension — never a `gen_ai.*` key). Token counts use the standard
`gen_ai.usage.*` attributes.

### How do I capture prompts/responses (and why are they missing)?

They're **off by default** (P7). Opt in with `OTelExporter(capture_content=True)` — then
`gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.system_instructions` are
emitted as JSON. Run the redaction interceptor (feat-008) first if the content may
contain PII.

### How do I keep traces stitched across an agent-to-agent call?

Use the propagation helpers: the caller `inject(trace_id, span_id, headers)` before the
hop; the callee `extract(headers)` and opens its span as a child — one end-to-end trace.
