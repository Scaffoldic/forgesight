# feat-017: FastAPI integration

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-017 |
| **Title** | FastAPI integration — ASGI middleware + lifespan flush; request↔run correlation |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-fastapi` |
| **Depends on** | feat-002, feat-010 |
| **Blocks** | none |

---

## 1. Why this feature

Most agents reach production as an HTTP service — a FastAPI app exposing
`POST /chat` or `POST /agents/{id}/run`. The two telemetry problems that follow
are universal and annoying:

- **Correlation.** A request comes in, the handler kicks off an agent run, the
  agent produces a span tree — but nothing ties the HTTP request to the run. An
  operator looking at a slow/failing request in their APM has no link to the
  agent run, its cost, or its tool calls. Each team wires `run_id ↔ request`
  by hand, differently.
- **Lost telemetry on deploy.** The SDK buffers records in a bounded queue and
  flushes on a timer (feat-003). When the process is told to shut down — a
  rolling deploy, a SIGTERM from Kubernetes, an autoscaler scaling in — the
  in-flight batch is dropped unless someone called `force_flush()` /
  `shutdown()`. Teams discover this the first time a deploy eats the traces for
  the exact run they were debugging.

A third, quieter pain: the agent run should continue an *incoming* distributed
trace. When a gateway or an upstream service already started a trace and passes
`traceparent`, the agent run should be a child of it — not a new disconnected
root. Hand-rolled middleware almost never extracts the incoming context
correctly.

## 2. Why this belongs in the SDK (vs each team wiring it by hand)

- **The middleware is small but the details are unforgiving.** Opening the
  right span (`agent_run` vs `workflow_run`), binding it to the request,
  extracting `traceparent` into the parent context, attaching route/method as
  business metadata, and closing the span with the response status — every team
  re-derives this and gets the propagation or the error path subtly wrong.
  Shipping it once makes request↔run correlation a *contract*, identical across
  every agent service.
- **Flush-on-shutdown is a correctness invariant, not a nicety.** "Telemetry is
  not lost on a clean deploy" is the kind of guarantee that must live below the
  app — if each team remembers to wire the lifespan hook, some won't, and they
  will lose exactly the telemetry they need during an incident. The SDK owns
  the lifecycle (`configure()` on startup, `force_flush()` + `shutdown()` on
  shutdown) so the guarantee holds by installation, not by discipline.
- **Continuing an incoming trace is a fleet-wide property.** Distributed
  tracing only works if *every* hop extracts and continues context. One service
  that drops the incoming `traceparent` breaks the trace for everything behind
  it. Centralising extraction in the middleware removes that failure mode.
- **Anti-pattern if left to teams:** N bespoke middlewares, inconsistent
  request↔run linking, half of them leaking request bodies into spans
  (violating P7), and the recurring "we lost the traces on the deploy" bug.

Framework-agnostic in spirit (P3): it observes a web framework, ships as its
own package wrapping one target (FastAPI/ASGI, P1/P2), and is never added to
core.

## 3. How consuming agents/teams benefit

- **Before:** an agent author writes a `BaseHTTPMiddleware` subclass (~40–60
  lines), manually generates a correlation id, threads it into the agent call,
  remembers to `force_flush()` in a shutdown handler (or doesn't), and writes
  bespoke `traceparent` extraction. **After:** three lines —
  `app.add_middleware(ForgeSightMiddleware)` and pass the SDK lifespan — and
  every request opens a correctly-correlated run span, continues the incoming
  trace, and flushes cleanly on shutdown.
- **Request↔run link for free.** The HTTP request and the agent run share a
  `trace_id`; the response carries the `run_id` (header) so a user-reported
  "request X was slow" jumps straight to the run's span tree and cost.
- **Zero lost telemetry on deploy.** The lifespan hook calls `force_flush()` +
  `shutdown()` on SIGTERM, so a rolling deploy never eats the buffered batch.
  The author writes no shutdown code.
- **Distributed traces just work.** An upstream `traceparent` is continued
  automatically; the agent service is a child span, not a new root — no
  propagation code in the app.
- **Route-level business metadata for free.** `http.route`, `http.method`, and
  status land as span attributes / metadata (FR-5), so per-endpoint cost and
  latency are queryable without instrumenting handlers individually.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — the entire wiring (~3 lines of integration)
from fastapi import FastAPI
from forgesight_fastapi import ForgeSightMiddleware, sdk_lifespan

app = FastAPI(lifespan=sdk_lifespan)          # configure() on startup, flush on shutdown
app.add_middleware(ForgeSightMiddleware)       # request → agent_run span, correlation

@app.post("/agents/pr-reviewer/run")
async def run(req: ReviewRequest):
    # Unchanged agent code. The current run span is already open and bound to
    # this request; the agent's llm/tool/mcp calls nest under it automatically.
    return await pr_reviewer.run(req.task)
```

```python
# Compose with your own lifespan if you already have one:
from contextlib import asynccontextmanager
from forgesight_fastapi import sdk_lifespan

@asynccontextmanager
async def lifespan(app):
    async with sdk_lifespan(app):       # SDK configure/flush wraps your setup
        await connect_db()
        yield
        await close_db()

app = FastAPI(lifespan=lifespan)
```

```typescript
// typescript — Fastify/Express-style (parity sketch)
import Fastify from 'fastify';
import { agentForgePlugin } from '@agentforge/sdk-fastify';

const app = Fastify();
await app.register(agentForgePlugin);   // request→run span + onClose flush
```

### 4.2 Public API / contract

```python
# forgesight_fastapi/__init__.py

class ForgeSightMiddleware:
    """ASGI middleware: opens an agent_run (or workflow_run) span per request,
    continues an incoming W3C trace, attaches route/method metadata, sets the
    run_id response header, and closes the span with the response status.
    """
    def __init__(
        self,
        app: "ASGIApp",
        *,
        span_kind: "Literal['agent_run', 'workflow_run']" = "agent_run",
        agent_name: "str | Callable[[Request], str]" = "fastapi-app",
        exclude_paths: "Sequence[str]" = ("/health", "/healthz", "/metrics", "/docs", "/openapi.json"),
        include_routes: "Sequence[str] | None" = None,   # None ⇒ all (minus exclude)
        capture_content: bool | None = None,             # P7: None ⇒ inherit global (off)
        run_id_header: str = "x-forgesight-run-id",
    ) -> None: ...

@asynccontextmanager
async def sdk_lifespan(app: "FastAPI") -> "AsyncIterator[None]":
    """Lifespan: forgesight.configure() on startup;
    force_flush() + shutdown() on shutdown. Composable with a user lifespan.
    """
```

```typescript
// @agentforge/sdk-fastify
export interface AgentForgeOptions {
  spanKind?: 'agent_run' | 'workflow_run';
  agentName?: string | ((req: FastifyRequest) => string);
  excludePaths?: string[];
  includeRoutes?: string[];
  captureContent?: boolean;
  runIdHeader?: string;
}
export const agentForgePlugin: FastifyPluginAsync<AgentForgeOptions>;
```

Stability: `ForgeSightMiddleware` constructor kwargs and `sdk_lifespan` are the
public surface, **stable** for 0.2. New optional kwargs may be added with safe
defaults (P5).

### 4.3 Internal mechanics

```
incoming request
   │  middleware: path in exclude_paths? ⇒ pass through, no span
   │  extract traceparent + tracestate ⇒ parent context (continue the trace)
   │  open span via feat-002 runtime:
   │     agent_run (default) or workflow_run, as a CHILD of the incoming context
   │     attrs: http.route, http.method, http.target ⇒ business metadata (FR-5)
   │  bind TelemetryContext to this request (contextvars survive the handler's awaits)
   ▼
   handler runs — agent.run()'s llm/tool/mcp calls nest under this span
   ▼
   response:
   │  set span status from HTTP status (>=500 ⇒ ERROR; 4xx ⇒ recorded, not errored)
   │  on unhandled exception ⇒ error.type + span ERROR (FR-7), re-raise
   │  set the run_id response header
   │  close span ⇒ record enqueued (non-blocking, feat-003)
```

The middleware uses the SDK's instrumentation API (feat-002), so the request
span is an ordinary `agent_run`/`workflow_run` and everything downstream nests
naturally — no special-casing. `route` resolution uses FastAPI's matched route
template (`/agents/{id}/run`), not the raw path, so cardinality stays bounded.

**Lifespan.** `sdk_lifespan` calls `forgesight.configure()` on startup
(idempotent; respects an already-configured SDK) and, on the shutdown phase,
`force_flush(timeout)` then `shutdown(timeout)` so the buffered batch is drained
before the worker stops. Because ASGI servers (uvicorn/hypercorn) run the
lifespan shutdown on SIGTERM, a rolling deploy flushes cleanly. The
`force_flush` timeout is bounded (feat-003 `export_timeout_millis`) so a wedged
backend can't hang the shutdown.

**Content gating (P7).** Request/response bodies are **not** captured unless
`capture_content` resolves true; only structural metadata (route, method,
status) is captured by default.

### 4.4 Module packaging

`forgesight-fastapi` is its own integration package wrapping exactly one
target (FastAPI / ASGI) and is **never** added to core (P1/P3). It depends on
`forgesight-core` + `starlette`/`fastapi` (its single framework
dependency). Core gains no web-framework dependency.

```bash
pip install forgesight-fastapi
```

```yaml
# forgesight.yaml
integrations:
  fastapi:
    enabled: true
    span_kind: agent_run        # or workflow_run
    exclude_paths: ["/health", "/metrics"]
    capture_content: false
```

Entry-point: `forgesight.integrations` →
`fastapi = forgesight_fastapi:install`. The middleware + lifespan are
imported explicitly in the app (ASGI middleware can't be auto-injected), but
config defaults are read from `forgesight.yaml`.

### 4.5 Configuration

| Key | Env | Default | Meaning |
|---|---|---|---|
| `integrations.fastapi.span_kind` | `FORGESIGHT_FASTAPI_SPAN_KIND` | `agent_run` | Open `agent_run` or `workflow_run` per request. |
| `integrations.fastapi.include_routes` | — | all | Route templates to instrument (allow-list). Mutually exclusive with relying solely on exclude. |
| `integrations.fastapi.exclude_paths` | `FORGESIGHT_FASTAPI_EXCLUDE` | `/health,/healthz,/metrics,/docs,/openapi.json` | Paths that get no span (health checks, docs, scrapes). |
| `integrations.fastapi.capture_content` | `FORGESIGHT_FASTAPI_CAPTURE_CONTENT` | `false` | Capture request/response bodies. Off by default (P7). |
| `integrations.fastapi.run_id_header` | `FORGESIGHT_FASTAPI_RUN_ID_HEADER` | `x-forgesight-run-id` | Response header carrying the run_id for correlation. |

Validation: `span_kind` must be `agent_run`|`workflow_run`; `exclude_paths`
are matched as prefixes; `capture_content: true` logs INFO once.

## 5. Plug-and-play & upgrade story

Add later: `pip install forgesight-fastapi`, swap to `lifespan=sdk_lifespan`
and add the middleware line — handlers unchanged (P2). Remove by reverting those
two lines + uninstalling; the app keeps serving. Minor upgrades add optional
kwargs behind defaults; the middleware/lifespan signatures stay (P5).

## 6. Cross-language parity

Identical: request→run span semantics, incoming-trace continuation, route/method
metadata, the run_id response header, flush-on-shutdown guarantee, content-gate
default, exclude/include semantics. Differs: Python ships ASGI middleware +
`sdk_lifespan` for Starlette/FastAPI; TS ships a Fastify plugin (and an Express
middleware variant) with `onClose` flush. Express/Fastify split is a TS-side
idiom, not a semantic difference.

## 7. Test strategy

- **Unit:** middleware opens the configured span kind; route template (not raw
  path) used; exclude/include honoured; run_id header set on the response.
- **Propagation:** incoming `traceparent` continued — the request span is a
  child of the upstream context, same `trace_id`.
- **Error path:** 5xx ⇒ span ERROR + `error.type`; unhandled exception recorded
  and re-raised; 4xx recorded without erroring the span.
- **Lifespan flush:** simulate shutdown ⇒ `force_flush()` + `shutdown()` called;
  buffered records reach the in-memory exporter; bounded timeout respected.
- **Content gate (P7):** bodies absent by default; present only with
  `capture_content`.
- **Integration:** a real FastAPI app (`TestClient`) running an instrumented
  agent end-to-end; assert request↔run share a trace.
- **Conformance:** feat-011 span-tree assertions against the in-memory exporter.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Lifespan shutdown not firing on hard SIGKILL | Document: clean flush requires SIGTERM/graceful drain; `atexit` (feat-003) is the backstop, but SIGKILL is unrecoverable by design. |
| Route cardinality blowing up metrics | Use the matched route *template*, not the raw path; document the exclude list for high-churn paths. |
| `BaseHTTPMiddleware` vs pure-ASGI semantics (streaming, background tasks) | Implement as pure-ASGI middleware to avoid `BaseHTTPMiddleware`'s streaming/lifespan pitfalls. |
| User already has a lifespan | `sdk_lifespan` is an async context manager designed to wrap a user lifespan; documented compose pattern. |
| Request body capture leaking PII | Off by default (P7); gated before pipeline; redaction interceptor first. |

## 9. Out of scope

- **General ASGI/HTTP auto-instrumentation** (every route, DB call, outbound
  client) — that's `opentelemetry-instrumentation-fastapi` / the OTel ecosystem;
  we compose with it and focus on the agent run, not replace it.
- **WebSocket / SSE streaming spans** beyond opening one run span per
  connection — first-class streaming spans are a follow-up.
- **Auth / OIDC for exporters** — that's the exporter's concern (and feat-018
  for CI); this feature is request↔run correlation + lifecycle.
- **A FastAPI-specific dashboard** — emit only (requirements §11).

## 10. References

- [`../requirements.md`](../requirements.md) — FR-1 (run tracking), FR-5 (business metadata), §5 personas (agent developer, SRE)
- [`../design/architecture.md`](../design/architecture.md) §5 (packaging), §7 (lifecycle), §11 (relationship to consumers)
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.6 (force_flush / shutdown)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.5 (W3C propagation)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P2, P3, P6, P7
- feat-002 (runtime / instrumentation API), feat-010 (configure / bootstrap)
