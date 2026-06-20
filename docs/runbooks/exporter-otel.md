# OpenTelemetry exporter runbook

> Ship ForgeSight records as OTLP spans mapped onto the GenAI semantic conventions, to any OTLP-compatible backend. **Extra:** `pip install "forgesight[otel]"` · **Selects with:** `exporters=["otel"]` · **Spec:** [feat-004](../features/feat-004-opentelemetry-exporter-and-semconv-mapping.md)

## What it does

`OTelExporter` turns each ForgeSight `Record` into an OpenTelemetry `ReadableSpan` (carrying ForgeSight's own trace/span ids) and hands it to an OTLP span exporter. Attribute mapping lives entirely in `SemConvMapper`, the single source of truth for the wire format, which maps records onto the GenAI semantic conventions (pinned via `SEMCONV_COMMIT` / `SEMCONV_VERSION`). It runs on the export worker, never the hot path, and `export()` never raises (P6): on any failure it returns `ExportResult.FAILURE`.

## When to use it

- You already run, or want to run, an OpenTelemetry collector / OTLP backend (Jaeger, Tempo, SigNoz, New Relic, Arize Phoenix, Grafana, etc.).
- You want vendor-neutral, standards-based traces you can route anywhere without re-instrumenting.
- It is the base for `forgesight-langfuse`, which wraps it.
- **Not** for pull-based metrics dashboards — use `forgesight-prometheus` for `/metrics`.

## Install

```bash
pip install "forgesight[otel]"      # facade extra
pip install forgesight-otel          # standalone package
```

Sub-extra for gRPC transport (HTTP/protobuf works out of the box):

```bash
pip install "forgesight-otel[grpc]"  # adds opentelemetry-exporter-otlp-proto-grpc
```

## Configure

Constructor (`forgesight_otel.OTelExporter`):

```python
OTelExporter(
    *,
    endpoint: str | None = None,            # OTLP endpoint; None → OTel env defaults
    protocol: str = "http/protobuf",        # "http/protobuf" | "http" | "grpc"
    service_name: str = "forgesight-agent",
    capture_content: bool = False,          # P7 — content opt-in
    emit_legacy_system: bool = False,       # also emit legacy gen_ai.system
    headers: dict[str, str] | None = None,
    resource_attributes: dict[str, str] | None = None,
    span_exporter: SpanExporter | None = None,  # inject for tests (e.g. InMemorySpanExporter)
)
```

Relevant env vars (resolved by the config layer, feat-010):

| Key (`exporters[].config`) | Env | Default |
| --- | --- | --- |
| `endpoint` | `FORGESIGHT_OTEL_ENDPOINT` (falls back to `OTEL_EXPORTER_OTLP_ENDPOINT`) | OTel env default |
| `protocol` | `FORGESIGHT_OTEL_PROTOCOL` | `http/protobuf` |
| `service_name` | `FORGESIGHT_OTEL_SERVICE_NAME` | `forgesight-agent` |
| `capture_content` | `FORGESIGHT_CAPTURE_CONTENT` | `false` |
| `emit_legacy_system` | `FORGESIGHT_OTEL_EMIT_LEGACY_SYSTEM` | `false` |
| `headers` | `FORGESIGHT_OTEL_HEADERS` (`k=v,k=v`) | `{}` |

Minimal selection by name:

```python
import forgesight

forgesight.configure(
    exporters=["otel"],
    exporter_config={
        "otel": {
            "endpoint": "http://localhost:4318",
            "protocol": "http/protobuf",
            "service_name": "my-agent",
        }
    },
)
```

Equivalent `forgesight.yaml`:

```yaml
# forgesight.yaml
exporters: [otel]
exporter_config:
  otel:
    endpoint: "http://localhost:4318"
    protocol: "http/protobuf"
    service_name: "my-agent"
```

## What it emits

Span names come from `SemConvMapper.span_name`: `invoke_agent <name>`, `invoke_workflow <name>`, `chat <name>`, `execute_tool <name>`, MCP `tools/call <tool>` (custom `STEP` records keep their own name). Span kind is `CLIENT` for `LLM`/`MCP`, `INTERNAL` otherwise.

GenAI attribute keys (locked in `semconv.py`):

- Operation/agent: `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.conversation.id`.
- LLM: `gen_ai.provider.name` (canonical; legacy `gen_ai.system` only when `emit_legacy_system`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.response.time_to_first_chunk`, plus per-param `gen_ai.request.<key>`.
- Tokens: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.reasoning.output_tokens`.
- Tools/MCP: `gen_ai.tool.name`, `gen_ai.tool.type`, `gen_ai.tool.call.id`, `gen_ai.tool.description`, `mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`.
- Errors: `error.type` (and `error.code`) on failed runs.

**Cost** lands on the namespaced extension `forgesight.usage.cost_usd` (OTel defines no cost key). Every Resource also carries `forgesight.semconv_version` and `forgesight.run.id`. Content (`gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.system_instructions`) is emitted only when `capture_content=True`.

## Operate it

Bring up a local OTLP backend with the repo's root `docker-compose.yml` Jaeger all-in-one service:

```bash
docker compose up -d jaeger   # OTLP gRPC :4317, OTLP HTTP :4318, UI :16686
```

Point the exporter at `http://localhost:4318` (HTTP/protobuf) or `http://localhost:4317` with `protocol="grpc"`. Run your instrumented agent, then **verify** the traces arrived in the Jaeger UI at <http://localhost:16686> — select service `my-agent` (or `forgesight-agent`) and look for `invoke_agent` / `chat` spans.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| No spans in backend, agent unaffected | Wrong endpoint/port or backend down; `export()` returned `FAILURE` (never raises) | Check `endpoint`; for Jaeger use `:4318` HTTP or `:4317` gRPC; confirm container is up |
| `unknown protocol ...` `ValueError` at startup | `protocol` not one of `http`, `http/protobuf`, `grpc` | Set a valid protocol |
| gRPC import error | `protocol="grpc"` without the extra | `pip install "forgesight-otel[grpc]"` |
| No prompt/response content on spans | `capture_content` off by default (P7) | Set `capture_content=True` (and the content gate) intentionally |
| Only `gen_ai.provider.name`, dashboards expect `gen_ai.system` | Legacy key off by default | Set `emit_legacy_system=True` |
| Export failures but agent keeps running | By design — failures are counted and logged, the run is unaffected | Inspect `sdk_export_failures_total` / logs |

## Reference

- Feature spec: [feat-004](../features/feat-004-opentelemetry-exporter-and-semconv-mapping.md)
- Package: [`packages/forgesight-otel`](../../packages/forgesight-otel)
- Playbooks: [install](../playbooks/01-install.md) · [run locally with Docker](../playbooks/03-run-locally-with-docker.md) · [ship to a backend](../playbooks/04-ship-to-a-backend.md)
