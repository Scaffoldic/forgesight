# FastAPI integration runbook

> Correlate every HTTP request with an agent run span and flush telemetry cleanly on shutdown. **Extra:** `pip install "forgesight[fastapi]"` · **Spec:** [feat-017](../features/feat-017-fastapi-integration.md)

## What it does

Adds pure-ASGI middleware (`ForgeSightMiddleware`) that, per request, continues an incoming W3C
trace (or starts a root), opens an `agent_run` (or `workflow_run`) span via the runtime, binds it
so the handler's LLM/tool/MCP calls nest under it, attaches `http.route` / `http.method` / status
as business metadata, and sets a `run_id` response header for correlation. A companion lifespan
(`sdk_lifespan`) configures the SDK on startup and force-flushes + shuts down on SIGTERM so a
rolling deploy never drops the in-flight batch.

## When to use it

- You serve an agent behind FastAPI/Starlette and want one run span per request, correlated to
  the caller's trace.
- You want `http.route`-level business metadata (per-endpoint cost, latency) for free.
- You want guaranteed flush-on-shutdown during rolling deploys / SIGTERM.

## Install

```bash
pip install "forgesight[fastapi]"    # facade extra
# or the standalone package:
pip install forgesight-fastapi       # depends on forgesight-core + starlette>=0.37
```

## Set up

Wire the lifespan and the middleware:

```python
from fastapi import FastAPI
from forgesight_fastapi import ForgeSightMiddleware, sdk_lifespan

app = FastAPI(lifespan=sdk_lifespan)      # configure() on startup; force_flush()+shutdown() on stop
app.add_middleware(ForgeSightMiddleware)  # one agent_run span per request
```

Tuning the middleware:

```python
app.add_middleware(
    ForgeSightMiddleware,
    span_kind="workflow_run",                 # one of SPAN_KINDS = ("agent_run", "workflow_run")
    agent_name="pr-reviewer",                 # str or callable(Request) -> str
    exclude_paths=["/health", "/metrics"],    # defaults to DEFAULT_EXCLUDE_PATHS (below)
    capture_content=False,                    # opt-in request/response body capture (off by default)
)
```

`DEFAULT_EXCLUDE_PATHS = ("/health", "/healthz", "/metrics", "/docs", "/openapi.json")` — these
prefixes are skipped so infra probes don't create runs. Override per-call, or via
`FORGESIGHT_FASTAPI_EXCLUDE` / the `integrations.fastapi` config block.

Composing `sdk_lifespan` inside your own lifespan, and flush-on-shutdown:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    async with sdk_lifespan(app, configure_sdk=True):   # **configure_kwargs flow to configure()
        # ... your own startup ...
        yield
        # on shutdown sdk_lifespan runs force_flush() then shutdown(), bounded by
        # flush_timeout_millis (defaults to the runtime's export_timeout_millis so a wedged
        # backend can't hang the shutdown)
```

The `forgesight.integrations` entry point (group `forgesight.integrations`, name **`fastapi`** →
`forgesight_fastapi:install`) stashes the `integrations.fastapi` config block as middleware
defaults. ASGI middleware can't be auto-injected, so you still call `add_middleware` yourself —
`install()` only supplies the defaults it reads.

W3C: the middleware extracts `traceparent` from the request headers (`extract_parent`); a
missing/malformed header degrades to a new local root, never raises.

## What it emits / correlates

- **Span:** one per instrumented request — a `RunScope` (`agent_run`) or `WorkflowScope`
  (`workflow_run`), selected by `span_kind`. The handler's LLM/tool/MCP calls nest under it.
- **Business metadata:** `http.method`, `http.target`, `http.route` (a bounded route *template*
  like `/agents/{id}/run`, reconstructed from `path_params` to keep cardinality bounded), and
  `http.status_code`.
- **Request↔run correlation:** the `run_id` is written to the response header
  (default `x-forgesight-run-id`, the `DEFAULT_RUN_ID_HEADER`) so a caller can join its request to
  the run.
- **Errors:** a 5xx response synthesises `HTTPServerError` → span `ERROR` with `error.type`; 4xx is
  recorded; an unhandled exception is recorded as ERROR and re-raised (the response path is never
  swallowed).
- **Content:** request/response bodies captured only when `capture_content` resolves true (logged
  once when enabled).

## Operate it

Runtime requirements: `starlette>=0.37` and a configured ForgeSight runtime (handled by
`sdk_lifespan` on startup).

Verify:

1. Run the app with an OTLP exporter (`forgesight[otel]`) pointed at the Jaeger all-in-one in the
   root [`docker-compose.yml`](../../docker-compose.yml) (OTLP gRPC `:4317`, UI `:16686`).
2. `curl -i http://localhost:8000/your-endpoint` and confirm the `x-forgesight-run-id` response
   header is present.
3. In Jaeger (http://localhost:16686) find the request's span and check `http.route`,
   `http.method`, and `http.status_code` are set; confirm `/health` and `/metrics` produced no
   span.
4. Send SIGTERM (stop the server) and confirm the final batch is flushed (no dropped records).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| No span for a request | Path matches an `exclude_paths` prefix, or `include_routes` excludes it | Adjust `exclude_paths` / `include_routes` (or env `FORGESIGHT_FASTAPI_EXCLUDE`) |
| `http.route` shows raw IDs, high cardinality | Route template not reconstructed (no `path_params`) | Ensure the router matched; the template is built from `path_params` after matching |
| Telemetry lost on deploy | App not using `sdk_lifespan` (no flush on shutdown) | Pass `lifespan=sdk_lifespan` or compose it inside your lifespan |
| `ValueError: span_kind must be one of ...` | Invalid `span_kind` | Use `"agent_run"` or `"workflow_run"` (`SPAN_KINDS`) |
| Bodies not captured | `capture_content` off (secure by default) | Set `capture_content=True` / env `FORGESIGHT_FASTAPI_CAPTURE_CONTENT` |
| Shutdown hangs | Backend wedged | `flush_timeout_millis` bounds it (defaults to `export_timeout_millis`); export is non-blocking and `export()` returns failure, never raises |

## Reference

- Feature spec: [feat-017 FastAPI integration](../features/feat-017-fastapi-integration.md)
- Package: [`packages/forgesight-fastapi`](../../packages/forgesight-fastapi)
- Playbook: [Install ForgeSight](../playbooks/01-install.md)
- Playbook: [Instrument your agent](../playbooks/02-instrument-your-agent.md)
- Playbook: [Instrument a FastAPI service](../playbooks/05-instrument-a-fastapi-service.md)
