# forgesight-fastapi

The FastAPI integration for [ForgeSight](https://github.com/Scaffoldic/forgesight). Three
lines wire request↔run correlation, incoming-trace continuation, and flush-on-shutdown into
any FastAPI / Starlette app — no per-handler instrumentation.

```bash
pip install forgesight-fastapi
```

```python
from fastapi import FastAPI
from forgesight_fastapi import AgentForgeMiddleware, sdk_lifespan

app = FastAPI(lifespan=sdk_lifespan)        # configure() on startup, flush on shutdown
app.add_middleware(AgentForgeMiddleware)     # request → agent_run span, correlation

@app.post("/agents/pr-reviewer/run")
async def run(req: ReviewRequest):
    # Unchanged agent code. The run span is already open and bound to this request;
    # the agent's llm/tool/mcp calls nest under it automatically.
    return await pr_reviewer.run(req.task)
```

Compose with an existing lifespan:

```python
from contextlib import asynccontextmanager
from forgesight_fastapi import sdk_lifespan

@asynccontextmanager
async def lifespan(app):
    async with sdk_lifespan(app):
        await connect_db()
        yield
        await close_db()

app = FastAPI(lifespan=lifespan)
```

## What you get

- **Request↔run link.** The HTTP request and the agent run share a `trace_id`; the response
  carries the `run_id` (header `x-agentforge-run-id`), so "request X was slow" jumps straight
  to the run's span tree and cost.
- **Distributed traces just work.** An upstream `traceparent` is continued automatically —
  the agent service is a child span, not a new root. No propagation code in the app.
- **Zero lost telemetry on deploy.** `sdk_lifespan` calls `force_flush()` + `shutdown()` on
  the shutdown phase (which ASGI servers run on SIGTERM), with a bounded timeout so a wedged
  backend can't hang the deploy.
- **Route-level metadata for free.** The matched route *template* (`/agents/{id}/run`, not
  the raw path — bounded cardinality), method, and status land as span metadata (FR-5).
- **Correct error mapping.** 5xx ⇒ span ERROR + `error.type`; an unhandled exception is
  recorded and re-raised (FR-7); 4xx is recorded without erroring the span.

Implemented as **pure ASGI** (not `BaseHTTPMiddleware`) to avoid streaming/lifespan pitfalls.
Request/response bodies are captured only when `capture_content` resolves true (P7).

## Configuration

| Key | Env | Default |
|---|---|---|
| `span_kind` | `FORGESIGHT_FASTAPI_SPAN_KIND` | `agent_run` (or `workflow_run`) |
| `exclude_paths` | `FORGESIGHT_FASTAPI_EXCLUDE` | `/health,/healthz,/metrics,/docs,/openapi.json` |
| `capture_content` | `FORGESIGHT_FASTAPI_CAPTURE_CONTENT` | `false` |
| `run_id_header` | `FORGESIGHT_FASTAPI_RUN_ID_HEADER` | `x-agentforge-run-id` |

Constructor kwargs win over env / `forgesight.yaml` (`integrations.fastapi`).

## License

Apache-2.0
