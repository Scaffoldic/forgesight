# feat-015: Datadog exporter + OTLP-native backend notes

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-015 |
| **Title** | Datadog exporter (DD APM / OTLP intake) + OTLP-native backend notes |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | both |
| **Module package(s)** | `forgesight-datadog` |
| **Depends on** | feat-004 |
| **Blocks** | none |

---

## 1. Why this feature

Datadog is the incumbent observability platform for a large share of enterprises,
and a team that already pays for Datadog wants its agent telemetry *there* — in
the same APM trace view, the same metrics explorer, the same dashboards and
monitors as the rest of its stack, tagged with `service`, `env`, and `version` so
it slots into existing Datadog conventions.

There are two ways in, and they differ in richness:

- **The generic OTLP way:** Datadog's Agent and intake accept OTLP. Pointing
  `forgesight-otel` at the DD Agent's OTLP port gets agent spans into Datadog
  with no Datadog-specific code — the same keystone path every OTLP backend gets.
- **The Datadog-native way:** Datadog's richest experience uses its own
  conventions (`service`/`env`/`version` unified tags, DD-trace span tagging, DD
  metrics, cost as a metric/tag you can build a monitor on). That last mile —
  mapping the SDK's spans and the SDK's *computed cost* onto Datadog's idioms — is
  DD-specific and is what this first-party package adds.

This is the one vendor in the 0.2 backend set that earns a dedicated package
*despite* OTLP working, because its richest path is genuinely DD-specific. The
feature also serves as the canonical place to state, loudly, that the **other**
common backends do **not** need a package.

## 2. Why this belongs in the SDK ecosystem (vs each team integrating the backend by hand)

- **The DD-native mapping is value the OTLP path can't give, and it depends on the
  SDK's model.** Datadog's unified `service`/`env`/`version` tags, its span
  resource/operation conventions, and cost-as-a-DD-metric are all functions of the
  SDK's domain model and computed cost (feat-006). Owning the mapping once means
  every team's agent shows up in Datadog with identical tags and an identical cost
  metric — comparable across teams, which is the SDK's purpose (requirements
  §1.1). Hand-rolled, each team's DD tags diverge and cost becomes a per-team
  hack.
- **It keeps the keystone honest.** Stating in one authoritative place that
  Honeycomb / Jaeger / Tempo / SigNoz / New Relic / X-Ray / Phoenix need **no
  package** (just `forgesight-otel`) prevents the anti-pattern of someone
  spinning up `forgesight-honeycomb` that is a thin, redundant wrapper over
  OTLP. The package set stays minimal *by design* (`architecture.md` §2, P4).
- **Foundation invariants.** The exporter implements the `TelemetryExporter`
  Protocol (`architecture.md` §4.2), runs on the pipeline worker (never the hot
  path), is fault-isolated (a DD Agent outage is caught, counted, invisible to the
  agent — P6 / NFR-3), gates content behind `capture_content` (P7), and passes the
  exporter conformance suite (feat-011).
- **Anti-pattern it prevents:** copy-pasted `ddtrace` setup and ad-hoc
  `statsd.gauge("cost", …)` calls sprinkled through agent code, with per-team tag
  schemes — the bespoke glue the SDK exists to replace.

A first-party package only because Datadog's richest path is DD-specific — exactly
the architecture's stated bar for a dedicated package (`architecture.md` §2). It is
not on the core; it wraps exactly one vendor SDK (P1).

## 3. How consuming agents/teams benefit

- **Before:** a team wires `ddtrace`, picks its own tag names, hand-emits a cost
  metric, and ends up with agent spans that don't match the rest of its DD APM
  conventions.
- **After:** `pip install forgesight-datadog`, add `datadog` to the exporters
  list with `api_key` + `site` (or a DD Agent endpoint). Agent runs appear in DD
  APM with unified `service`/`env`/`version` tags; LLM/tool/MCP calls as child
  spans; token usage and the SDK's **computed cost** as DD metrics/tags you can
  build a monitor on.
- **Cost monitors for free.** `forgesight.cost_usd` lands as a DD metric, so a
  team builds a "spend > $X/hr" monitor in Datadog with no extra plumbing — the
  same cost the SDK reports everywhere (feat-006).
- **No package for OTLP-native backends.** A team on Honeycomb/Jaeger/Tempo/
  SigNoz/New Relic/X-Ray/Phoenix just points `forgesight-otel` at it — one
  config line, zero new packages (§4.3).
- **Swap is config.** Datadog today, Honeycomb tomorrow — drop `datadog`, add the
  OTLP endpoint, no agent-code change (requirements §10.4). Fan out to several at
  once (FR-11).

## 4. Feature specifications

### 4.1 User-facing experience

```bash
pip install forgesight-datadog
```

```python
# python
import forgesight
forgesight.configure()      # resolves "datadog" from the exporters list

# or explicit
from forgesight_datadog import DatadogExporter
forgesight.configure(exporters=[
    DatadogExporter(api_key="${DD_API_KEY}", site="datadoghq.com",
                    service="issue-classifier", env="prod"),
])
```

```yaml
# forgesight.yaml — preferred
exporters:
  - name: datadog
    config:
      api_key: "${DD_API_KEY}"
      site: "datadoghq.com"            # or datadoghq.eu, us3/us5/ap1, gov
      service: "issue-classifier"
      env: "prod"
      # agent_endpoint: "http://datadog-agent:8126"   # via local DD Agent instead of intake
```

```yaml
# forgesight.yaml — OTLP-native path to Datadog (NO datadog package needed)
exporters:
  - name: otlp                          # from forgesight-otel
    config:
      protocol: "grpc"
      endpoint: "http://datadog-agent:4317"   # DD Agent's OTLP intake
```

```typescript
// typescript
import { configure } from '@agentforge/sdk';
import { DatadogExporter } from '@agentforge/sdk-datadog';
configure({ exporters: [new DatadogExporter({ apiKey: process.env.DD_API_KEY!, site: 'datadoghq.com', service: 'issue-classifier', env: 'prod' })] });
```

### 4.2 Public API / contract

`DatadogExporter` implements the locked `TelemetryExporter` Protocol
(`architecture.md` §4.2), registered under the entry point name `datadog`, and
must pass the exporter conformance suite (feat-011).

```python
# forgesight_datadog/exporter.py
from collections.abc import Sequence
from forgesight_api import Record, ExportResult, TelemetryExporter

class DatadogExporter(TelemetryExporter):
    """Maps SDK records → Datadog APM spans + DD metrics (incl. cost), via DD
    intake or a local DD Agent. Stable from v0.2."""

    def __init__(
        self,
        *,
        api_key: str | None = None,           # required for direct intake
        site: str = "datadoghq.com",
        service: str = "agentforge",
        env: str | None = None,
        version: str | None = None,
        agent_endpoint: str | None = None,    # use a local DD Agent instead of intake
        transport: str = "agent",             # "agent" (ddtrace via DD Agent) | "otlp" (DD OTLP intake)
    ) -> None: ...

    # --- TelemetryExporter Protocol (locked) ---
    def export(self, records: Sequence[Record]) -> ExportResult: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
```

**Record → Datadog mapping:**

| SDK record | Datadog | Notes |
|---|---|---|
| `AgentRun` / `WorkflowRun` | APM span (root) | `service`, `env`, `version` unified tags; `resource = agent_name`; `run_id` as a span tag |
| `Step` / `LLMCall` / `ToolCall` / `MCPCall` | child APM spans | DD span tags from `gen_ai.*` attrs; latency as span duration |
| token usage | DD metric `forgesight.tokens` | tagged `gen_ai_token_type`, `provider`, `model` |
| `forgesight.usage.cost_usd` | DD metric `forgesight.cost_usd` (+ span tag) | monitorable; equals the SDK's computed cost (feat-006) |
| `status` / `error.type` | span status + `error.*` tags | (FR-7) |

**Stability:** class name, constructor keywords, config keys, and the mapping are
stable from v0.2; new optional kwargs arrive with defaults (P5).

### 4.3 Internal mechanics

```
records ──► DatadogExporter.export()
              │  transport == "agent": ddtrace span writer → DD Agent (:8126) → DD APM
              │                         + DD metrics (cost/tokens) via the Agent
              │  transport == "otlp":  OTLP → DD Agent (:4317) or DD OTLP intake
              ▼
           Datadog APM + Metrics (service/env/version unified tags)
```

- **Two transports.** `"agent"` uses `ddtrace` to write to a local DD Agent
  (the conventional production deployment) and emits DD metrics for cost/tokens.
  `"otlp"` sends OTLP to the DD Agent's OTLP port or DD's OTLP intake — useful when
  a team prefers OTLP end-to-end but still wants DD-native tagging applied by the
  exporter. Both honour `service`/`env`/`version` (`DD_*` unified tags).
- **Cost is the SDK's, surfaced as a DD signal.** `forgesight.usage.cost_usd`
  (feat-006) is emitted as the DD metric `forgesight.cost_usd` (and a span tag) so
  it is dashboardable and monitorable — the same number every other backend shows.
- **Content gating + redaction.** Prompt/completion/tool-arg content is attached
  to spans **only when `capture_content` is on** (P7), after the redaction
  interceptor (feat-008).
- **Pipeline-resident, fault-isolated.** `export()` runs on the pipeline worker
  with a batch (`exporter-pipeline.md` §4.3); a DD Agent/intake outage → `FAILURE`,
  counted in `sdk_export_failures_total`, never raised (P6); the hot path never
  blocks (NFR-2).

---

#### OTLP-native backends need **no dedicated package**

This is the load-bearing note. Because the domain model maps cleanly onto the OTel
GenAI conventions, the **OTel exporter is the keystone** (`architecture.md` §2,
P4): anything that ingests OTLP works through `forgesight-otel` with **no
dedicated package**. Concretely, **none of these get a package** — point
`forgesight-otel` at them and you are done:

| Backend | How to send | Package needed |
|---|---|---|
| **Honeycomb** | `forgesight-otel` → `api.honeycomb.io:443` + `x-honeycomb-team` header | **none** |
| **Jaeger** | `forgesight-otel` → Jaeger OTLP `:4317` | **none** |
| **Grafana Tempo** | `forgesight-otel` → Tempo OTLP endpoint | **none** |
| **SigNoz** | `forgesight-otel` → SigNoz OTLP collector | **none** |
| **New Relic** | `forgesight-otel` → `otlp.nr-data.net:4317` + `api-key` header | **none** |
| **AWS X-Ray** | `forgesight-otel` → AWS Distro for OTel (ADOT) collector | **none** |
| **Arize Phoenix** | `forgesight-otel` → Phoenix OTLP endpoint | **none** |

A first-party package is justified **only** when a backend's richest path is
backend-specific and the raw OTLP path leaves real value on the table — the bar
the 0.2 set meets: Prometheus (pull `/metrics`, feat-012), Langfuse (native
observation/cost model, feat-013), ClickHouse (columnar schema, feat-014), and
**Datadog** (DD-native APM tagging + cost-as-DD-metric, this feature). Datadog is
the deliberate exception in the "OTLP-native, no package" list precisely because
its DD-native mapping is worth shipping; a team that only wants generic spans in
Datadog can still use the OTLP path above and skip this package entirely.

### 4.4 Module packaging

An **integration package — one backend, one vendor SDK** (`architecture.md` §5),
wrapping exactly **one** vendor SDK: `ddtrace` (with `datadog`/`datadog-api-client`
for metrics as needed, all within this package's dependency set). Per P1 these are
**never** added to `forgesight-core`; they live only here.

| Package | Provides | Deps |
|---|---|---|
| `forgesight-datadog` | `DatadogExporter` (DD APM + cost metric) + the OTLP-native-backends note | `forgesight-core`, `ddtrace` |

```toml
# forgesight_datadog/pyproject.toml
[project]
dependencies = ["forgesight-core>=0.2", "ddtrace>=2"]

[project.entry-points."forgesight.exporters"]
datadog = "forgesight_datadog.exporter:DatadogExporter"
```

The OTLP-native path (to Datadog *or* any backend in §4.3) needs **only**
`forgesight-otel` — no entry here. Installing this package makes `datadog`
resolvable by name from config (`architecture.md` §6, path 1). No core change.

### 4.5 Configuration

`exporters[].config` + `FORGESIGHT_*` / standard `DD_*` env; constructor
kwargs win (FR-12). Named + defaulted (P8).

| Key | Env | Default | Validation |
|---|---|---|---|
| `api_key` | `DD_API_KEY` / `FORGESIGHT_DATADOG_API_KEY` | `null` | required for direct intake; not for local Agent; never logged |
| `site` | `DD_SITE` / `FORGESIGHT_DATADOG_SITE` | `datadoghq.com` | one of DD sites (`datadoghq.com`, `datadoghq.eu`, `us3/us5/ap1`, `ddog-gov.com`) |
| `service` | `DD_SERVICE` / `FORGESIGHT_DATADOG_SERVICE` | `agentforge` | unified-tag service name |
| `env` | `DD_ENV` / `FORGESIGHT_DATADOG_ENV` | `null` | unified-tag env |
| `version` | `DD_VERSION` / `FORGESIGHT_DATADOG_VERSION` | `null` | unified-tag version |
| `agent_endpoint` | `FORGESIGHT_DATADOG_AGENT_ENDPOINT` | `null` | DD Agent URL (e.g. `http://datadog-agent:8126`); when set, intake `api_key` not required |
| `transport` | `FORGESIGHT_DATADOG_TRANSPORT` | `agent` | `agent` \| `otlp` |

Validation: `transport: otlp` requires an `agent_endpoint` (or DD OTLP intake URL);
direct intake (`transport: agent` with no `agent_endpoint`) requires `api_key` —
otherwise fail-fast at `configure()` (`architecture.md` §8). Content capture is the
SDK-wide `capture_content` gate (P7), not configured here.

## 5. Plug-and-play & upgrade story

**OTLP-native (any backend, incl. Datadog for generic spans):** already covered by
`forgesight-otel` — add the endpoint + auth header, no new package.
**DD-native:** `pip install forgesight-datadog` + the `exporters` block, no
agent-code change (P2); remove by dropping the package + config. `ddtrace` is
pinned in this package, so a DD SDK bump never touches callers. Class name + config
keys stable from v0.2; new knobs arrive as optional defaults (P5).

## 6. Cross-language parity

Identical across Python / TypeScript: the record→DD mapping, the two transports,
unified `service`/`env`/`version` tags, cost-as-DD-metric, and config keys
(`architecture.md` §10) — and the OTLP-native-backends note applies in both
languages. Allowed to differ: the vendor SDK (`ddtrace` Python vs `dd-trace-js`),
async idioms, naming. TypeScript targets parity by 0.4.

## 7. Test strategy

- **Unit:** record→DD-span mapping; unified-tag injection (`service`/`env`/
  `version`); cost emitted as `forgesight.cost_usd` metric + span tag equal to the
  SDK's computed cost; content omitted unless `capture_content`; transport
  selection (`agent` vs `otlp`) and its validation.
- **Conformance (feat-011):** exporter conformance suite — non-raising `export`,
  idempotent `force_flush`/`shutdown`, fault isolation (DD Agent down ⇒ counted,
  not raised).
- **Integration:** against a local DD Agent container (skips if absent) — assert a
  run produces an APM trace tree and a `forgesight.cost_usd` metric; OTLP transport
  reaches the Agent's OTLP port.
- **OTLP-native-backends doc test:** assert the §4.3 table's endpoints/headers are
  what the OTel exporter would send (no DD package on that path).
- **Example agent:** one run to Datadog DD-native; build a cost monitor query
  against `forgesight.cost_usd`.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Teams building redundant `-honeycomb`/`-jaeger`/etc. packages | The §4.3 table + keystone rule: OTLP-native backends get **no** package. |
| DD Agent vs direct-intake confusion | `transport` + `agent_endpoint`; validation fails fast on a bad combo (§4.5). |
| Cost double-counted (DD's LLM Obs vs SDK cost) | We emit the SDK's computed cost as a clearly-namespaced `forgesight.cost_usd`; documented as the source of truth. |
| `ddtrace` global tracer collisions with app APM | Exporter uses an isolated writer/config; documented; OTLP transport avoids it entirely. |
| Prompt/PII on DD spans | Content gated by `capture_content` (P7) + redaction (feat-008) first. |
| DD site/region misconfig | `site` validated against the known DD site list (§4.5). |

## 9. Out of scope

- **Datadog LLM Observability product-specific features** beyond span + cost
  mapping (we emit the standard model; DD LLM Obs can ingest it).
- **Datadog dashboards / monitors.** We emit metrics + spans; dashboards and
  monitors are built in Datadog (requirements §11).
- **Packages for OTLP-native backends** (Honeycomb, Jaeger, Tempo, SigNoz, New
  Relic, X-Ray, Phoenix) — deliberately none; use `forgesight-otel` (§4.3).
- **Reading data back from Datadog.** Export only; the SDK is a client.
- **Capturing content by default.** Off unless `capture_content` (P7).

## 10. References

- [`../design/architecture.md`](../design/architecture.md) §2 (keystone + when a first-party package is justified), §4.2 (SPI), §5 (packages)
- [`../design/design-principles.md`](../design/design-principles.md) P1, P2, P4, P6, P7, P10
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2–4.4 (span/metric mapping), §4.5 (W3C propagation)
- [`../design/cost-model.md`](../design/cost-model.md) (the cost surfaced as `forgesight.cost_usd`)
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.3 (worker), §4.4 (fault isolation)
- [`../requirements.md`](../requirements.md) FR-3, FR-6, FR-7, FR-9, FR-11, NFR-2, NFR-3
- feat-004 (OTLP exporter / GenAI mapping — the keystone this builds on), feat-006 (cost), feat-008 (interceptors), feat-011 (conformance)
- Prior art: `ddtrace`, Datadog OTLP intake / DD Agent OTLP, Datadog unified service tagging
