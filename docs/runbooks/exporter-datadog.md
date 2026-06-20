# Datadog exporter runbook

> Maps ForgeSight records onto Datadog APM spans plus the monitorable DD metrics `forgesight.cost_usd` and `forgesight.tokens`, with unified `service`/`env`/`version` tags, via a local DD Agent or OTLP intake. **Extra:** `pip install "forgesight[datadog]"` · **Selects with:** `exporters=["datadog"]` · **Spec:** [feat-015](../features/feat-015-datadog-exporter.md)

## What it does

`DatadogExporter` surfaces agent telemetry in Datadog APM: the run as a root span, LLM/tool/MCP calls as child spans, all carrying the unified `service`/`env`/`version` tags and `gen_ai.*` attributes. The SDK's computed cost is emitted as the DD metric `forgesight.cost_usd` (and a span tag) so you can build a "spend > $X/hr" monitor on it; token usage lands as `forgesight.tokens`. It runs on the export worker, never the hot path, and is the one OTLP-native backend in the 0.2 set that still earns a package because its richest path is DD-specific.

## When to use it

- You already pay for Datadog and want agent telemetry in the same APM view, metrics explorer, dashboards, and monitors as the rest of your stack.
- You want a cost/token monitor built on `forgesight.cost_usd` / `forgesight.tokens` with no ad-hoc statsd plumbing.
- You want DD-native unified tagging (`service`/`env`/`version`) applied consistently across teams.
- **Not** for Honeycomb / Jaeger / Tempo / SigNoz / New Relic / X-Ray / Phoenix — those are OTLP-native and need **no** package; point `forgesight-otel` at them. If you only want generic spans in Datadog, you can also use the OTLP path (DD Agent OTLP port) and skip this package.

## Install

```bash
pip install "forgesight[datadog]"      # extra on the umbrella package
# or the standalone integration package:
pip install forgesight-datadog
```

Installing makes the name `datadog` resolvable from config via the `forgesight.exporters` entry point. The package wraps the `ddtrace` (>=2) vendor SDK (and reuses `forgesight-otel` for its OTLP transport); these deps live only here, never on the core.

## Configure

Constructor (all keyword-only; env fills the gaps):

```python
DatadogExporter(
    api_key=None,           # DD_API_KEY / FORGESIGHT_DATADOG_API_KEY — required for direct intake
    site="datadoghq.com",   # DD_SITE / FORGESIGHT_DATADOG_SITE
    service="agentforge",   # DD_SERVICE / FORGESIGHT_DATADOG_SERVICE
    env=None,               # DD_ENV / FORGESIGHT_DATADOG_ENV
    version=None,           # DD_VERSION / FORGESIGHT_DATADOG_VERSION
    agent_endpoint=None,    # FORGESIGHT_DATADOG_AGENT_ENDPOINT — e.g. http://datadog-agent:8126
    transport="agent",      # FORGESIGHT_DATADOG_TRANSPORT — "agent" | "otlp"
    capture_content=False,  # gated by the SDK-wide capture_content (P7)
)
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `api_key` | `DD_API_KEY` / `FORGESIGHT_DATADOG_API_KEY` | `None` | required for direct intake; never logged |
| `site` | `DD_SITE` / `FORGESIGHT_DATADOG_SITE` | `datadoghq.com` | one of `datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`, `datadoghq.eu`, `ap1.datadoghq.com`, `ddog-gov.com` |
| `service` | `DD_SERVICE` / `FORGESIGHT_DATADOG_SERVICE` | `agentforge` | unified-tag service name |
| `env` | `DD_ENV` / `FORGESIGHT_DATADOG_ENV` | `None` | unified-tag env |
| `version` | `DD_VERSION` / `FORGESIGHT_DATADOG_VERSION` | `None` | unified-tag version |
| `agent_endpoint` | `FORGESIGHT_DATADOG_AGENT_ENDPOINT` | `None` | DD Agent URL; when set, intake `api_key` not required |
| `transport` | `FORGESIGHT_DATADOG_TRANSPORT` | `agent` | `agent` (ddtrace → DD Agent) \| `otlp` (OTLP → DD Agent / intake) |

**Transport choice:**

- `transport="agent"` (default) maps each record to a DD APM span written via `ddtrace` to a local DD Agent (conventional production deployment, agent trace port `:8126`) and emits the cost/token DD metrics via dogstatsd. Direct intake (no `agent_endpoint`) requires `api_key`.
- `transport="otlp"` reuses `forgesight-otel`'s `OTelExporter` (http/protobuf) pointed at `agent_endpoint` — the DD Agent's OTLP port (`:4318` HTTP / `:4317` gRPC) or DD's OTLP intake — applying `env`→`deployment.environment` and `version`→`service.version` as resource attributes Datadog reads. `transport="otlp"` requires `agent_endpoint` (fails fast otherwise).

The `site` is validated against the known DD site list; an unknown site fails fast at `configure()`.

Select it by name with `exporter_config`:

```python
import forgesight

forgesight.configure(
    exporters=["datadog"],
    exporter_config={
        "datadog": {
            "api_key": "${DD_API_KEY}",
            "site": "datadoghq.com",
            "service": "issue-classifier",
            "env": "prod",
            "transport": "agent",
            # "agent_endpoint": "http://datadog-agent:8126",  # via local DD Agent
        },
    },
)
```

Equivalent `forgesight.yaml`:

```yaml
exporters:
  - name: datadog
    config:
      api_key: "${DD_API_KEY}"
      site: "datadoghq.com"          # or datadoghq.eu, us3/us5/ap1, ddog-gov.com
      service: "issue-classifier"
      env: "prod"
      transport: "agent"
      # agent_endpoint: "http://datadog-agent:8126"   # local DD Agent instead of intake
```

OTLP-native path to Datadog (no `forgesight-datadog` package needed — uses `forgesight-otel`):

```yaml
exporters:
  - name: otlp
    config:
      protocol: "grpc"
      endpoint: "http://datadog-agent:4317"   # DD Agent's OTLP intake
```

## What it emits

| SDK record | Datadog | Notes |
|---|---|---|
| `AgentRun` / `WorkflowRun` | root APM span (`forgesight.agent` / `forgesight.workflow`) | `service`/`env`/`version` unified tags; `resource = agent_name`; `forgesight.run_id` span tag |
| `Step` / `LLMCall` / `ToolCall` / `MCPCall` | child spans (`forgesight.step` / `.llm` / `.tool` / `.mcp`) | DD span tags from `gen_ai.*`; latency = span duration |
| token usage | DD metric `forgesight.tokens` | tagged `gen_ai_token_type`, `service`, `env`, `provider`, `model` |
| `forgesight.usage.cost_usd` | DD metric `forgesight.cost_usd` (+ `forgesight.cost_usd` span tag) | monitorable; equals the SDK's computed cost |
| `status` / error | span `error` flag + `error.type` / `error.message` tags | |

DD span `name` is the per-kind operation (`forgesight.agent`, `forgesight.llm`, …) and `resource` is the agent name / model / tool / MCP method. The `agent` transport writes spans via `ddtrace` and emits metrics via dogstatsd; the `otlp` transport delegates to `forgesight-otel`. Content (prompt/completion/tool-arg) is attached to spans only when `capture_content` is on, after redaction.

## Operate it

You need a DD Agent (or OTLP intake) reachable from the agent process.

- **DD Agent OTLP endpoint vs direct intake:** run a local DD Agent (the conventional deployment). For `transport="agent"`, ddtrace writes to the Agent's trace port `:8126`; for `transport="otlp"`, point `agent_endpoint` at the Agent's OTLP port (`:4318` HTTP / `:4317` gRPC). Direct intake skips the Agent but requires `api_key` and the correct `site`.
- **Find your spans in Datadog APM:** open APM → Traces and filter by `service:<your service>` (e.g. `service:issue-classifier`) and `env:<env>`. Root spans appear as `forgesight.agent` / `forgesight.workflow` with child `forgesight.llm` / `.tool` / `.mcp` spans. In Metrics, search `forgesight.cost_usd` and `forgesight.tokens` to build dashboards/monitors (e.g. a "spend > $X/hr" monitor on `forgesight.cost_usd`).

The exporter uses an isolated writer/config to avoid colliding with an app's own `ddtrace` global tracer; the OTLP transport avoids that surface entirely.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `configure()`: `unknown Datadog site` | `site` not in the known list | use a valid site (`datadoghq.com`, `datadoghq.eu`, `us3/us5/ap1`, `ddog-gov.com`) |
| `transport='otlp' requires agent_endpoint` | OTLP transport without an endpoint | set `agent_endpoint` to the DD Agent OTLP port or DD OTLP intake |
| `transport='agent' direct intake requires api_key` | agent transport, no Agent endpoint, no key | set `api_key`, or point `agent_endpoint` at a local DD Agent |
| `transport must be 'agent' or 'otlp'` | bad `transport` value | use `agent` or `otlp` |
| No spans in DD APM | wrong service filter, or Agent/intake unreachable | filter `service:<service>`; check the Agent / endpoint |
| Cost looks doubled vs DD LLM Obs | DD's own cost vs the SDK's | treat `forgesight.cost_usd` as the source of truth |
| DD Agent down; agent keeps running | the exporter is fault-isolated | expected — see the guarantee below |

**Non-blocking guarantee:** `export()` never raises (P6). A DD Agent / intake outage is caught, returns `ExportResult.FAILURE`, is counted by the pipeline (`sdk_export_failures_total`) and logged at WARN; the agent run is unaffected and the hot path never blocks.

## Reference

- Spec: [feat-015](../features/feat-015-datadog-exporter.md)
- Package: [`../../packages/forgesight-datadog`](../../packages/forgesight-datadog)
- Playbook: [install](../playbooks/01-install.md)
- Playbook: [run locally with Docker](../playbooks/03-run-locally-with-docker.md)
- Playbook: [ship to a backend](../playbooks/04-ship-to-a-backend.md)
