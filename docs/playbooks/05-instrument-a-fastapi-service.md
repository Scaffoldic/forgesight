# Playbook 05 — Instrument a FastAPI service

> Goal: correlate every HTTP request with the agent run it triggers, and flush telemetry
> cleanly on deploy/shutdown — without per-route boilerplate.

## Install

```bash
pip install "forgesight[fastapi,otel]"     # the integration + a backend to ship to
```

## Wire it in (two lines)

```python
from fastapi import FastAPI
from forgesight_fastapi import AgentForgeMiddleware, sdk_lifespan
import forgesight

forgesight.configure(
    service_name="agent-api",
    exporters=["otel"],
    exporter_config={"otel": {"endpoint": "http://localhost:4318", "protocol": "http/protobuf"}},
)

app = FastAPI(lifespan=sdk_lifespan)        # flush-on-shutdown
app.add_middleware(AgentForgeMiddleware)    # request <-> run correlation
```

- `sdk_lifespan` calls `force_flush()` + `shutdown()` on app shutdown (bounded by
  `export_timeout_millis`), so a rolling deploy never drops in-flight telemetry.
- `AgentForgeMiddleware` is pure ASGI: it opens/links a run per request and attaches
  `http.route`, `http.method`, `http.target`, `http.status_code`. 5xx responses are recorded
  as an `HTTPServerError`.

## Your handlers stay clean

```python
from forgesight import telemetry

@app.post("/review")
async def review(pr: PRRequest):
    # the request is already correlated; just instrument your work as usual
    with telemetry.agent_run("pr-reviewer", version="2.1.0") as run:
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            ...
    return {"ok": True}
```

## Skip noise paths

Health checks and docs are excluded by default (`DEFAULT_EXCLUDE_PATHS`). Override:

```python
app.add_middleware(AgentForgeMiddleware, exclude_paths=["/healthz", "/metrics", "/internal"])
```

## Run & verify

```bash
docker compose up -d jaeger                      # from the repo root compose
uvicorn app:app --port 8000
curl -X POST localhost:8000/review -d '{"pr": 42}' -H 'content-type: application/json'
```

Open http://localhost:16686, service `agent-api` → you'll see a request-scoped trace with the
`pr-reviewer` run and its LLM span nested under it.

## Notes

- Trace context propagates in via the `x-agentforge-run-id` header (configurable), so an
  upstream caller's run id can thread through.
- Export is non-blocking — a backend outage never delays a response or breaks a request.

Full reference: [FastAPI integration runbook](../runbooks/fastapi-integration.md).

## Next

→ [06 — Instrument GitHub Actions](./06-instrument-github-actions.md)
