# Langfuse exporter runbook

> Export ForgeSight records to Langfuse over OTLP, enriched with native `langfuse.*` observation attributes. **Extra:** `pip install "forgesight[langfuse]"` · **Selects with:** `exporters=["langfuse"]` · **Spec:** [feat-013](../features/feat-013-langfuse-exporter.md)

## What it does

`LangfuseExporter` wraps `forgesight-otel`'s `OTelExporter` pointed at Langfuse's OTLP ingest endpoint (`<host>/api/public/otel`, HTTP/protobuf, Basic auth) and enriches each record with the native `langfuse.*` attributes Langfuse reads. LLM calls land as `generation` observations, tools as `tool`, steps as `span`, agents/workflows as `agent`/`chain` — with the SDK's computed `forgesight.usage.cost_usd` ingested. It runs on the pipeline worker, content is captured only when `capture_content` is on (P7), and `export()` never raises (P6).

## When to use it

- You use Langfuse (cloud or self-hosted) for LLM tracing, eval, and cost dashboards.
- You want first-party `langfuse.*` enrichment (observation types, trace name, user/session/tags) without hand-writing OTLP.
- **Not** needed if you only want raw OTLP — you can point `forgesight-otel` straight at `<host>/api/public/otel` with a Basic-auth header (no `forgesight-langfuse` package).

## Install

```bash
pip install "forgesight[langfuse]"   # facade extra
pip install forgesight-langfuse       # standalone package
```

It depends on `forgesight-otel` (transport), so no extra OTLP install is needed. There is no sub-extra.

## Configure

Constructor (`forgesight_langfuse.LangfuseExporter`):

```python
LangfuseExporter(
    *,
    public_key: str,                     # required (pk-lf-...)
    secret_key: str,                     # required (sk-lf-...); never logged
    host: str | None = None,             # explicit host wins over region
    region: str | None = None,           # "us" | "eu"; resolves host if host unset
    capture_content: bool = False,       # P7 — content opt-in
    span_exporter: SpanExporter | None = None,  # inject for tests
)
```

Host resolution: an explicit `host` wins; otherwise `region` maps `us → https://us.cloud.langfuse.com`, `eu → https://cloud.langfuse.com`; with neither it defaults to the EU host. Missing `public_key`/`secret_key` fails fast at construction (`ValueError`), never mid-run.

Relevant env vars (resolved by the config layer, feat-010):

| Key (`exporters[].config`) | Env | Default |
| --- | --- | --- |
| `public_key` | `FORGESIGHT_LANGFUSE_PUBLIC_KEY` | — (required) |
| `secret_key` | `FORGESIGHT_LANGFUSE_SECRET_KEY` | — (required) |
| `host` | `FORGESIGHT_LANGFUSE_HOST` | resolved from `region`, else EU host |
| `region` | `FORGESIGHT_LANGFUSE_REGION` | `null` |
| `capture_content` | `FORGESIGHT_CAPTURE_CONTENT` | `false` |

Minimal selection by name:

```python
import forgesight

forgesight.configure(
    exporters=["langfuse"],
    exporter_config={
        "langfuse": {
            "public_key": "pk-lf-...",
            "secret_key": "sk-lf-...",
            "host": "https://cloud.langfuse.com",   # or region: "eu"
        }
    },
)
```

Equivalent `forgesight.yaml`:

```yaml
# forgesight.yaml — first-party path (preferred)
exporters: [langfuse]
exporter_config:
  langfuse:
    public_key: "${LANGFUSE_PUBLIC_KEY}"   # pk-lf-...
    secret_key: "${LANGFUSE_SECRET_KEY}"   # sk-lf-...
    host: "https://cloud.langfuse.com"     # or self-hosted / region URL
```

OTLP-native alternative (no `forgesight-langfuse` package — uses `otel` directly):

```yaml
exporters: [otel]
exporter_config:
  otel:
    endpoint: "https://cloud.langfuse.com/api/public/otel"
    protocol: "http/protobuf"
    headers:
      Authorization: "Basic ${LANGFUSE_OTLP_BASIC_AUTH}"   # base64(pk-lf-…:sk-lf-…)
```

## What it emits

Transport and GenAI attributes are exactly `forgesight-otel`'s (spans named `invoke_agent`/`chat`/`execute_tool`/…, `gen_ai.*` attributes, `forgesight.usage.cost_usd`). On top, each record gets `langfuse.observation.type` mapped from its kind:

| Record kind | `langfuse.observation.type` |
| --- | --- |
| `AGENT` | `agent` |
| `WORKFLOW` | `chain` |
| `STEP` | `span` |
| `LLM` | `generation` |
| `TOOL` | `tool` |
| `MCP` | `tool` |

On the root span of an `AGENT`/`WORKFLOW` (no parent), it sets `langfuse.trace.name` to the record name and lifts business metadata into trace-level attributes: `user_id → langfuse.user.id`, `session_id → langfuse.session.id`, `tags → langfuse.trace.tags`. Token counts and cost are carried via the underlying GenAI/`forgesight.usage.cost_usd` attributes, which Langfuse reads as generation usage and cost. Prompts/completions appear only when `capture_content=True`.

## Operate it

Langfuse is a hosted/self-hosted backend (no service in the repo's `docker-compose.yml`). Use Langfuse Cloud (EU `https://cloud.langfuse.com` or US `https://us.cloud.langfuse.com`) or your own self-hosted instance; the OTLP ingest path is always `<host>/api/public/otel`. Create a project, generate `pk-lf-…` / `sk-lf-…` keys, and set them via env or `exporter_config`.

**Verify** by running an instrumented agent, then opening the Langfuse UI for your project: a new **trace** named after your agent should appear, with nested `generation` observations on LLM calls showing model, token usage, and cost.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ValueError: LangfuseExporter requires public_key and secret_key` at startup | Missing/empty keys | Set `public_key` and `secret_key` (env or config) |
| 401 / 403 at ingest, no traces | Wrong keys or wrong region host | Verify keys belong to the project; match `host`/`region` to the keys' region |
| Traces land in the wrong region | `region` unset and host defaulted to EU | Set `region: "us"` or an explicit `host` |
| Observations show no prompts/responses | `capture_content` off by default (P7) | Set `capture_content=True` intentionally |
| Self-hosted instance not receiving data | `host` not pointed at your instance | Set `host: "https://<self-host>"`; ingest hits `<host>/api/public/otel` |
| Export failures but agent keeps running | By design — failures are counted and logged via the wrapped OTel exporter; the run is unaffected | Inspect `sdk_export_failures_total` / logs |

## Reference

- Feature spec: [feat-013](../features/feat-013-langfuse-exporter.md)
- Package: [`packages/forgesight-langfuse`](../../packages/forgesight-langfuse)
- Playbooks: [install](../playbooks/01-install.md) · [run locally with Docker](../playbooks/03-run-locally-with-docker.md) · [ship to a backend](../playbooks/04-ship-to-a-backend.md)
