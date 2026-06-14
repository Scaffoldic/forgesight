# feat-002: Telemetry runtime & instrumentation API

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-002 |
| **Title** | Telemetry runtime & instrumentation API |
| **Status** | `in-progress` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` (import root `forgesight_core`), `forgesight` (import root `forgesight`) |
| **Depends on** | feat-001 |
| **Blocks** | feat-004, feat-005, feat-006, feat-007, feat-008, feat-009, feat-016, feat-017, feat-018, feat-019 |

---

## 1. Why this feature

feat-001 gives you the *vocabulary* (the domain model + SPIs). It does nothing
on its own — there is no way yet to actually *start a run*, attach an LLM call to
it, build a span tree, or get a `Record` flowing toward an exporter. feat-002 is
the runtime that turns "a set of dataclasses" into "instrument my agent in under
ten lines."

The pain it removes is the boilerplate every team writes by hand today:

- **Threading the run id through everything.** Without ambient context, every
  function that wants to log against the current run takes a `run_id` parameter,
  and someone forgets to pass it across an `await`, and the trace tree breaks.
  Teams hand-roll a `ContextVar` and a half-correct propagation scheme, usually
  losing context across `asyncio.gather` or a `create_task`.
- **Building the span tree by hand.** "This LLM call is a child of this step,
  which is a child of this run" is parent/child plumbing that gets re-implemented,
  inconsistently, per agent — and gets the parent wrong under concurrency.
- **Wiring start/finish into records.** Capturing start time, filling status and
  duration on exit (including on the *exception* path), pricing the LLM call,
  emitting the lifecycle events — every team rebuilds this, and most forget the
  error path.

The success criterion for the whole SDK is **"any agent instrumented in < 10
lines"** ([requirements §1.2](../requirements.md#12-goals)). feat-002 is the
feature that has to deliver that line count.

## 2. Why this belongs in the SDK core (vs each agent/team rolling its own)

- **Correct context propagation is genuinely hard and must be written once.**
  Surviving nested `async` tasks, `asyncio.gather`, `create_task`, and thread
  hops with `contextvars` is subtle — the failure mode (a child span attaching to
  the wrong parent, or to no parent) is silent and only shows up as a broken
  trace in production. Shipping one correct implementation (P9) means *every*
  agent gets correct nesting for free; leaving it to each agent guarantees a long
  tail of subtly-broken traces that are uncomparable.
- **The instrumentation surface IS the contract every adapter targets.** feat-016
  (MCP), feat-017 (FastAPI), feat-018 (GitHub), and feat-019 (framework adapters)
  all instrument by calling `agent_run` / `step` / `llm_call` / `tool_call` /
  `mcp_call`. If this surface isn't owned centrally, each integration invents its
  own and the span trees diverge — the exact fragmentation the SDK exists to end.
- **Non-blocking is an invariant the runtime must guarantee, not hope for**
  (P6, NFR-2). The hot path must build a record and hand it off *without* I/O.
  That guarantee is only meaningful if the runtime owns the boundary between "fill
  the record" (hot path) and "export it" (the feat-003 pipeline). An agent that
  rolls its own runtime will, eventually, do a synchronous network call on the
  hot path and stall the agent.
- **Business metadata at run/step/call scope (FR-5) needs one propagation rule.**
  "Metadata set at run scope appears on every child; metadata set at call scope
  appears only on that call" is a single, testable rule. If each agent invents
  metadata propagation, cost attribution and chargeback (the platform team's
  whole reason for adopting the SDK) become unreliable.

**Anti-pattern if left to each agent:** a per-team `ContextVar` + ad-hoc span
plumbing that breaks across `gather`, gets the parent wrong under concurrency,
forgets the error path, and occasionally blocks the agent on a slow exporter —
multiplied by every team.

## 3. How agents/teams consuming the SDK benefit

- **Instrumentation in well under 10 lines.** *Before:* ~60–120 lines of
  `ContextVar` setup, span-tree plumbing, start/finish timing, error handling,
  and a token-to-cost call, per agent. *After:*

  ```python
  import forgesight
  forgesight.configure()
  with forgesight.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
      with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
          resp = client.messages.create(...)
          call.record_usage(input=resp.usage.input_tokens, output=resp.usage.output_tokens)
  ```

  That's the whole thing — span tree, ids, timing, status, cost, events, export
  handoff, all handled.
- **A `@instrument` decorator turns an existing function into a tool span with
  one line.** *Before:* wrap the body in try/finally, time it, build a `ToolCall`,
  set status on error. *After:* `@instrument(kind="tool")` above the function.
- **Concurrency just works.** Fan out ten tool calls with `asyncio.gather` and
  each lands as a child of the right step — no `run_id` parameter threading, no
  lost context across `await` (the propagation is `contextvars`-correct, P9).
- **Business metadata is one call, propagated correctly.**
  `run.set_metadata(team="platform", repo="agentforge")` once at run scope tags
  every child span; per-call metadata stays on that call (FR-5). FinOps gets
  per-team cost without the agent author thinking about it.
- **The decision about *where telemetry goes* is deferred entirely.** The agent
  author calls the instrumentation API; the platform team picks the exporter at
  deploy time (feat-003/004/010). Changing backends never touches this code.

## 4. Feature specifications

### 4.1 User-facing experience

The minimal, **< 10-line** developer experience — the success criterion made
concrete:

```python
# python — the whole thing
import forgesight

forgesight.configure()                                   # 1 (feat-010; console by default)

with forgesight.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.step("react-iter-1"):
        with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
            resp = client.messages.create(model="claude-sonnet-4-5", messages=msgs)
            call.record_usage(input=resp.usage.input_tokens,
                              output=resp.usage.output_tokens,
                              cache_read=resp.usage.cache_read_input_tokens)
        with run.tool_call("web_search", tool_type="function"):
            results = search(query)
# on exit: status=ok, duration filled, LLM call priced, RUN_COMPLETED emitted, records exported
```

The decorator form, for instrumenting an existing function:

```python
from forgesight import instrument

@instrument(kind="tool", name="web_search")     # one line → a tool span on every call
def web_search(query: str) -> list[str]:
    ...

@instrument(kind="agent", version="1.2.0")      # wraps the whole function in an agent_run
async def classify(issue: str) -> str:
    ...
```

Concurrency — context propagates across `gather`, every call nests correctly:

```python
async with forgesight.telemetry.agent_run("fan-out") as run:
    async with run.step("parallel-tools"):
        await asyncio.gather(
            call_tool("search"),     # each opens run.tool_call(...) internally;
            call_tool("fetch"),      # each lands as a child of "parallel-tools"
            call_tool("summarise"),  # no run_id threading, correct parents
        )
```

Business metadata (FR-5):

```python
with forgesight.telemetry.agent_run("classifier") as run:
    run.set_metadata(team="platform", repo="agentforge", environment="prod")  # → every child
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.set_metadata(prompt_variant="B")        # → only this LLM span
```

```typescript
// typescript — parity, idiomatic
import { configure, telemetry, instrument } from '@agentforge/sdk';

configure();
await telemetry.agentRun('issue-classifier', { version: '1.2.0' }, async (run) => {
  await run.step('react-iter-1', async () => {
    await run.llmCall({ provider: 'anthropic', model: 'claude-sonnet-4-5' }, async (call) => {
      const resp = await client.messages.create({ /* … */ });
      call.recordUsage({ input: resp.usage.input_tokens, output: resp.usage.output_tokens });
    });
  });
});
```

(TS uses callback scopes rather than `with` because it has no context-manager
syntax; semantics — nesting, ids, timing, export — are identical.)

### 4.2 Public API / contract

**Stable (locked)** unless annotated **experimental**. The instrumentation
surface is the contract feat-016/017/018/019 build on, so it locks early (P5).

#### The facade — `forgesight.telemetry` — **locked**

```python
# forgesight_core/runtime/facade.py  (re-exported as forgesight.telemetry)
from contextlib import AbstractContextManager
from types import TracebackType

class Telemetry:
    def agent_run(
        self, name: str, *, version: str | None = None,
        parent_run_id: str | None = None, context_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> "RunScope": ...

    def workflow_run(
        self, name: str, *, metadata: dict[str, object] | None = None,
    ) -> "WorkflowScope": ...

    def current_run(self) -> "RunScope | None": ...     # the active run, or None

telemetry: Telemetry          # module-level singleton (configured by configure(), feat-010)
```

`RunScope` / `WorkflowScope` are async-and-sync context managers (they implement
both `__enter__/__exit__` and `__aenter__/__aexit__`):

```python
# forgesight_core/runtime/scope.py — locked surface
class RunScope:
    run_id: str                 # ULID, generated on enter
    trace_id: str               # W3C trace id
    parent_run_id: str | None

    # --- nesting ---
    def step(self, name: str, *, metadata: dict | None = None) -> "StepScope": ...
    def llm_call(self, provider: str, model: str, *,
                 metadata: dict | None = None) -> "LLMScope": ...
    def tool_call(self, name: str, *, tool_type: str = "function",
                  call_id: str | None = None,
                  metadata: dict | None = None) -> "ToolScope": ...
    def mcp_call(self, server: str, method: str, *, tool: str | None = None,
                 session_id: str | None = None,
                 metadata: dict | None = None) -> "MCPScope": ...

    # --- metadata (FR-5) ---
    def set_metadata(self, **kv: object) -> None: ...        # run scope ⇒ all children

    # context-manager dunders (sync + async) populate status/timing on exit,
    # emit RUN_STARTED on enter and RUN_COMPLETED / RUN_FAILED on exit,
    # set status=ERROR + capture the exception on the exception path (FR-7).
    def __enter__(self) -> "RunScope": ...
    def __exit__(self, et, ev, tb) -> bool: ...             # returns False (re-raises)
    async def __aenter__(self) -> "RunScope": ...
    async def __aexit__(self, et, ev, tb) -> bool: ...

class LLMScope:
    def record_usage(self, *, input: int = 0, output: int = 0,
                     cache_read: int = 0, cache_creation: int = 0,
                     reasoning: int = 0) -> None: ...
    def record_response(self, *, response_model: str | None = None,
                        finish_reasons: tuple[str, ...] = (),
                        response_id: str | None = None,
                        time_to_first_chunk_ms: float | None = None) -> None: ...
    def record_params(self, **params: object) -> None: ...   # temperature, max_tokens, top_p…
    def set_cost(self, cost_usd: float) -> None: ...         # provider-supplied cost (wins, FR-9)
    def set_metadata(self, **kv: object) -> None: ...        # this call only
    # on exit: price via PricingProvider if cost unset, fill latency, emit LLM_EXECUTED

class ToolScope:
    def set_metadata(self, **kv: object) -> None: ...
class MCPScope:
    def set_metadata(self, **kv: object) -> None: ...
class StepScope:
    def step(self, name: str, **kw) -> "StepScope": ...      # steps may nest
    def llm_call(self, *a, **kw) -> "LLMScope": ...          # same leaf calls as RunScope
    def tool_call(self, *a, **kw) -> "ToolScope": ...
    def mcp_call(self, *a, **kw) -> "MCPScope": ...
    def set_metadata(self, **kv: object) -> None: ...
```

`record_usage` / `record_response` / `record_params` / `set_cost` are **locked**
(they map 1:1 onto the locked `LLMCall` fields from feat-001). The exact set of
convenience helpers may grow (minor bump); the four shown are the floor.

#### The decorator — `forgesight.instrument` — **locked**

```python
# forgesight_core/runtime/decorator.py
from collections.abc import Callable
from typing import TypeVar, overload
from forgesight_api import Kind

F = TypeVar("F", bound=Callable[..., object])

def instrument(
    *, kind: Kind | str = Kind.TOOL,
    name: str | None = None,            # defaults to the wrapped function's __qualname__
    tool_type: str = "function",        # when kind == tool
    version: str | None = None,         # when kind == agent
    capture_args: bool = False,         # opt-in content (P7); off by default
) -> Callable[[F], F]: ...
```

Works on sync **and** async functions (detected via
`inspect.iscoroutinefunction`); wraps the call in the matching scope, opens it
under `current_run()` as parent, and fills status/timing/error on return or
raise. `capture_args=True` is the **opt-in** content gate (P7) — off by default;
captured args still flow through interceptors (feat-008) before export.

#### Context propagation primitives — `forgesight_core.context` — **stable**

```python
# forgesight_core/context.py
import contextvars
from forgesight_api import AgentRun

_CURRENT: contextvars.ContextVar["TelemetryContext | None"]

class TelemetryContext:
    run_id: str
    trace_id: str
    parent_run_id: str | None
    current_span_id: str | None         # the parent for the next child
    context_id: str | None
    metadata: dict[str, object]         # accumulated run/step-scope metadata (FR-5)

def current_context() -> TelemetryContext | None: ...
def new_run_id() -> str: ...            # ULID generator (the one place ids are minted)
```

`TelemetryContext` is the per-run ambient state propagated via `contextvars`
([architecture §3](../design/architecture.md#3-key-concepts)). Direct use is for
adapter authors (feat-019); app authors use the scopes.

#### TypeScript parity sketch — `@agentforge/sdk`

```typescript
export const telemetry: {
  agentRun<T>(name: string, opts: { version?: string; metadata?: Record<string, unknown> },
              body: (run: RunScope) => Promise<T>): Promise<T>;
  workflowRun<T>(name: string, opts, body): Promise<T>;
  currentRun(): RunScope | null;
};
export function instrument(opts: { kind?: Kind; name?: string; toolType?: string }):
  <F extends (...a: any[]) => any>(fn: F) => F;
```

Context propagation uses `AsyncLocalStorage` (the Node equivalent of
`contextvars`); the scope/leaf-call/metadata semantics are identical
([architecture §10](../design/architecture.md#10-cross-language-parity)).

### 4.3 Internal mechanics

#### Building the span tree

Each scope, on enter, mints a `span_id`, reads `current_context()` for the
parent (`current_span_id`), pushes itself as the new `current_span_id`, and on
exit pops back. Because the push/pop rides on `contextvars`, an `await` in the
middle preserves the right parent, and a `create_task` / `gather` child inherits
a *copy* of the context — so two concurrent tool calls each see the same step as
parent without racing each other's `current_span_id`.

```
telemetry.agent_run("classifier")            ── mints run_id (ULID), trace_id
  ctx = TelemetryContext(run_id, trace_id, current_span_id=run.span_id)
  set _CURRENT = ctx ; emit RUN_STARTED
  │
  ├─ run.step("react-iter-1")                 ── span_id_S, parent = run.span_id
  │    ctx.current_span_id = span_id_S
  │    │
  │    ├─ run.llm_call(...)                    ── span_id_L, parent = span_id_S
  │    │    record_usage/response/params; on exit price + emit LLM_EXECUTED
  │    │    build_record(LLMCall) → interceptors → queue   (feat-003)
  │    │
  │    └─ run.tool_call("web_search")          ── span_id_T, parent = span_id_S
  │         build_record(ToolCall) → interceptors → queue
  │    on step exit: ctx.current_span_id = run.span_id   (pop)
  │
  on run exit: status=ok|error, duration filled; price unpriced LLM calls;
               emit RUN_COMPLETED/RUN_FAILED; build_record(AgentRun) → queue
```

#### Id generation & propagation (FR-1)

| Id | Where minted | Propagation |
|---|---|---|
| `run_id` | `new_run_id()` (ULID) at `agent_run`/`workflow_run` enter | Lives in `TelemetryContext`; same for the whole run; rides as baggage / extension attr for log correlation. |
| `trace_id` | New W3C trace id at the **root** run; inherited by nested runs that share a trace | One trace can span nested `run_id`s; propagates across process/agent hops via W3C TraceContext (feat-004). |
| `parent_run_id` | Set when `agent_run(parent_run_id=…)` is passed, or auto-set when a run is opened inside another run's context | Links spawned / nested runs (FR-1). |
| `context_id` | Caller-supplied only (a real conversation/session id) | → `gen_ai.conversation.id`; never fabricated (spec forbids — [otel-semconv §4.3](../design/otel-semantic-conventions.md#43-attribute-mapping)). |
| `span_id` | Minted per scope on enter | The parent for the next child; push/pop on `contextvars`. |

ULIDs are minted in exactly one place (`new_run_id()`) so the format contract
from feat-001 is enforced centrally.

#### Business metadata scoping (FR-5)

Metadata is layered: `TelemetryContext.metadata` accumulates run- and step-scope
keys; each leaf scope keeps its own per-call dict. When a `Record` is built, the
runtime merges `context.metadata` (inherited) under the call's own metadata (call
wins on conflict). Result: run-scope metadata appears on every child span;
call-scope metadata appears only on that call — the exact acceptance criterion of
FR-5.

#### The hot path stays non-blocking (P6, NFR-2)

On scope exit the runtime does only CPU-bound work: fill terminal fields on the
live model, price the LLM call (O(1) dict hit, feat-006), `build_record()` →
freeze into an immutable `Record` (feat-001), run the interceptor chain
(feat-008), and `queue.put_nowait` into the feat-003 pipeline. **No network, no
await on an exporter, no lock held across I/O.** Everything past the queue
(batching, fan-out, the actual export) is the feat-003 worker's job. The
runtime's contract to the agent is: *enter and exit are O(#interceptors), never
I/O* (target < 5 ms p99, NFR-1).

The exception path is symmetric: `__exit__` with a non-None exception sets
`status = ERROR`, captures type/message/stack (feat-009), emits `RUN_FAILED`,
builds the record, and returns `False` so the exception re-raises to the caller
(FR-7 — telemetry never swallows the agent's exception).

### 4.4 Module packaging

- **Lives in:** `forgesight-core` (`forgesight_core`) holds the runtime —
  context, scopes, span-tree builder, decorator, `build_record()`. The
  `forgesight` (`forgesight`) facade **re-exports** `telemetry`,
  `instrument`, and `configure` as the batteries-included surface most users
  import ([architecture §5](../design/architecture.md#5-package-model-three-tiers--integrations)).
- **Dependencies:** `-core` depends on `forgesight-api` (feat-001),
  `opentelemetry-api` (the API only, never a vendor SDK — P1), and a small
  pure-Python set (a ULID helper); `-sdk` depends on `-core`. No backend SDK is a
  transitive dependency of either (NFR-6).
- **pip install:**

  ```bash
  pip install forgesight          # the facade — pulls -core and -api transitively
  ```

  `forgesight.yaml` snippet (the runtime needs no config to function; this just
  shows where it sits):

  ```yaml
  forgesight:
    service_name: "issue-classifier"
    exporters: ["console"]            # feat-010 resolves; runtime emits regardless
  ```
- **Entry-point group:** this feature defines the *call sites* that adapters
  target; adapters register under `forgesight.adapters` (feat-019). The
  runtime itself registers nothing — it is always-installed core.

### 4.5 Configuration

The runtime works with **zero config** (FR-12) — `agent_run` etc. function
immediately after `configure()`; with no exporter configured it routes to the
shipped `ConsoleExporter` (feat-003). The keys it reads (resolved by feat-010,
precedence env → YAML → kwargs, last wins):

| Key | Env | YAML | Default | Meaning |
|---|---|---|---|---|
| `service_name` | `FORGESIGHT_SERVICE_NAME` | `forgesight.service_name` | `"agentforge-agent"` | Resource service name on every span. |
| `capture_content` | `FORGESIGHT_CAPTURE_CONTENT` | `forgesight.capture_content` | `false` | Opt-in content/message capture (P7). Off ⇒ no prompt/completion/arg content ever leaves the runtime. |
| `default_tool_type` | `FORGESIGHT_DEFAULT_TOOL_TYPE` | `forgesight.default_tool_type` | `"function"` | Fallback `tool_type` when `tool_call` omits it. |

Validation: `service_name` is a non-empty string; `capture_content` is strictly
boolean (a truthy string like `"1"`/`"true"` is parsed, anything else errors at
`configure()` — fail fast, not mid-run). All thresholds elsewhere (queue/batch
sizes, sample rate) belong to the pipeline (feat-003); the runtime defines none of
its own magic numbers (P8). The instrumentation API never reads env vars
directly — it reads the resolved config object from feat-010.

## 5. Plug-and-play & upgrade story

`forgesight-core` and `forgesight` are always-installed (the runtime is
not optional — it's the thing every integration calls). There is no "add it
later" step; it's present the moment the SDK is.

Upgrade safety: the scope surface (`agent_run`/`step`/`llm_call`/`tool_call`/
`mcp_call`/`instrument`) is locked (P5). New convenience helpers on the scopes
(e.g. another `record_*`) arrive as minor bumps with safe defaults; existing
adapter and agent code survives a minor upgrade untouched. The
`TelemetryContext` shape may gain optional fields (minor); removing one is a
major bump + ADR.

## 6. Cross-language parity

**Identical:** the run/step/leaf-call model, id generation + propagation rules,
the span-tree shape, metadata scoping (FR-5), the non-blocking hot-path contract,
and the config keys
([architecture §10](../design/architecture.md#10-cross-language-parity)).

**Allowed to differ:** context-manager `with` blocks (Python) vs callback scopes
(TS) — TS has no `with`; `contextvars` vs `AsyncLocalStorage`; sync+async dunder
pairs (Python) vs `async`-only callbacks (TS). The decorator is `@instrument` in
Python and a higher-order wrapper in TS.

**Staging:** Python lands in 0.1; TypeScript targets the same surface by 0.2/0.4
per [ADR-0008](../adr/0008-python-first-multilanguage-parity.md).

## 7. Test strategy

- **Unit** — each scope opens/closes correctly; status is `OK` on clean exit,
  `ERROR` on exception (and the exception re-raises — FR-7); duration is filled;
  ULID `run_id` and W3C `trace_id` are well-formed; metadata merge follows
  run-wins-over-nothing / call-wins-over-run (FR-5).
- **Context propagation (the load-bearing test)** — span parents are correct
  across `await`, `asyncio.gather`, `create_task`, and nested steps; two
  concurrent `tool_call`s under one step both parent to that step and neither
  corrupts the other's `current_span_id`. Mirrors the feat-009-observability
  precedent ("`run_id` propagation across nested async tasks").
- **Span-tree snapshot** — run a representative agent against the `InMemoryExporter`
  (feat-003) and assert the exact tree: `agent.run → step → {llm, tool, mcp}`
  with the right ids and metadata.
- **Non-blocking** — assert the hot path performs no I/O (a fake exporter that
  sleeps does not delay scope exit; work happens on the worker, feat-003);
  benchmark scope enter+exit < 5 ms p99 (NFR-1).
- **Decorator** — `@instrument` on sync and async functions produces the right
  scope; `capture_args=False` (default) emits no arg content (P7).
- **Conformance** — the runtime feeds feat-011's span-tree assertion helpers.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| `contextvars` lost across a manually-spawned thread (not a task) | Document that thread hops need `contextvars.copy_context()`; provide a helper; covered by the propagation test suite. |
| App author forgets `record_usage` ⇒ no tokens/cost | LLM span still emitted with `usage=0`/`cost=None` (degrade gracefully, FR-9); a DEBUG note flags an LLM call with zero tokens. |
| Sync-only hosts (Spring bridge, plain scripts) on an async-first core | Scopes implement *both* sync and async dunders; sync usage is first-class, not a shim. Open question on a fully sync facade deferred to feat-019 ([design-principles §8](../design/design-principles.md#8-open-questions)). |
| `@instrument(capture_args=True)` leaking PII | Captured args pass through the interceptor chain (feat-008 redaction) before export; gate is off by default (P7). |
| Pricing on the hot path adding latency | Cost lookup is an O(1) dict hit after one regex-normalise (feat-006); well inside the 5 ms budget (NFR-1). |
| Mapping `Step` to a `plan` span vs a custom INTERNAL span | Resolved per [otel-semconv §4.2](../design/otel-semantic-conventions.md#42-span-mapping): custom step name as INTERNAL; `plan` only when semantically a plan (mapping is feat-004's job, not the runtime's). |

## 9. Out of scope

- **The export pipeline itself** — bounded queue, worker, batching, fan-out,
  flush/shutdown — is feat-003. The runtime stops at `queue.put_nowait`.
- **The OTel attribute/span mapping** — turning a `Record` into `gen_ai.*`
  attributes and span names is feat-004.
- **Metric emission** — counters/histograms are feat-005 (the runtime produces
  the records they aggregate).
- **Cost computation / the pricing table** — feat-006 (the runtime *calls*
  `PricingProvider`, doesn't implement it).
- **Interceptor implementations** (redaction, content gating, policy) — feat-008
  (the runtime *invokes* the chain).
- **Event listeners / the event bus delivery machinery** — feat-007 (the runtime
  *emits* lifecycle events into it).
- **Config loading / `configure()`** — feat-010 (the runtime *consumes* the
  resolved config).
- **Framework auto-instrumentation** — feat-019 adapters call this surface; the
  runtime privileges no framework (P3).

## 10. References

- [`architecture.md`](../design/architecture.md) §3 (TelemetryContext), §5
  (packaging), §7 (lifecycle), §9 (perf characteristics)
- [`design-principles.md`](../design/design-principles.md) — P3, P6, P7, P8, P9
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.2 (the hot path the
  runtime hands off to)
- [`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2
  (span mapping), §4.3 (attribute mapping), §4.5 (context propagation)
- [`cost-model.md`](../design/cost-model.md) §4.1 (the `PricingProvider` the
  runtime calls on LLM-scope exit)
- [ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md) — stable surface
- [ADR-0008](../adr/0008-python-first-multilanguage-parity.md) — Python-first
  parity
- Depends on: [feat-001](./feat-001-core-domain-model-and-contracts.md) (model +
  SPIs). Blocks: [feat-003](./feat-003-async-export-pipeline.md), feat-004,
  feat-005, feat-006, feat-007, feat-008, feat-009, feat-016, feat-017, feat-018,
  feat-019.
- Prior art: AgentForge `feat-009-observability` (run-id context propagation
  across nested async tasks); OpenLLMetry / OpenInference instrumentation
  surfaces.

---

## Implementation status

**Status: in-progress (Python).** Landing on
`feat/002-telemetry-runtime-and-instrumentation-api` — `forgesight-core` +
`forgesight` facade. 79 tests across both packages, **96.5% coverage**,
`mypy --strict` + `ruff` clean.

| Module | Scope |
|---|---|
| `forgesight_core/context.py` | `TelemetryContext` (+`.child()` copy), `contextvars` get/set/reset, `new_run_id` (ULID), `new_span_id` (16-hex). |
| `forgesight_core/scope.py` | `_Scope` (sync+async dunders, span-tree, status, events) → `RunScope`/`WorkflowScope`/`StepScope` (containers) + `LLMScope`/`ToolScope`/`MCPScope` (leaves); `current_run_scope()`; metadata scoping (FR-5); LLM pricing on exit. |
| `forgesight_core/processor.py` | `Runtime` dispatch singleton: interceptor chain (drop/replace/isolate), fault-isolated fan-out to exporters, ordered event delivery, `force_flush`/`shutdown`, drop/failure counters. |
| `forgesight_core/exporters.py` | `InMemoryExporter` (testing) + `ConsoleExporter` (zero-config default sink). |
| `forgesight_core/facade.py` | `Telemetry` facade (`agent_run`/`workflow_run`/`current_run`) + minimal `configure()`. |
| `forgesight_core/decorator.py` | `@instrument` (sync+async; agent/step/tool). |
| `forgesight` | Facade package re-exporting `configure`/`telemetry`/`instrument`/`current_run`. |

### Deviations from this spec

- **Synchronous dispatch placeholder.** §9 puts the bounded async queue + worker in
  feat-003. feat-002 ships a synchronous, fault-isolated `Runtime.emit_record`
  (interceptors → fan-out) so the runtime is testable end-to-end now; feat-003
  replaces the internals with the bounded queue + background worker behind the
  same `emit_record`/`emit_event` surface.
- **`InMemoryExporter` + `ConsoleExporter` shipped here**, not feat-003 — the
  runtime needs a default sink and tests need an in-memory one. Architecture §5
  places both in core; feat-003 will wrap them in the async pipeline.
- **Minimal `configure()`.** Full env/YAML resolution + entry-point exporter
  loading is feat-010; the call site (`forgesight.configure()`) is stable.
- **`current_run()` via a dedicated `contextvars` var** set by `RunScope`.
- **`@instrument` covers agent/step/tool**; `llm`/`mcp`/`workflow` need per-call
  args and are opened via the scope API (the decorator raises a clear error).
- **`capture_args` is accepted but inert** in feat-002 — the content-capture
  machinery (P7 gating + redaction) lands in feat-008.
- **Structured run fields** (`agent.version`, `parent.run_id`, `context.id`) are
  stashed in `Record.attributes` pending the OTel attribute mapping (feat-004).

### Not yet implemented

- The async bounded pipeline (feat-003), OTel mapping (feat-004), metrics
  (feat-005), pricing table (feat-006 — the runtime calls a `PricingProvider` if
  one is registered), event-bus formalisation (feat-007), interceptor built-ins
  (feat-008), error detail capture (feat-009), full config (feat-010).
- TypeScript port.

## Runbook

### How do I instrument an agent (the <10-line path)?

```python
import forgesight

forgesight.configure()                                   # console by default
with forgesight.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.step("react-iter-1"):
        with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
            resp = client.messages.create(...)
            call.record_usage(input=resp.usage.input_tokens, output=resp.usage.output_tokens)
        with run.tool_call("web_search"):
            results = search(query)
```

### How do I instrument an existing function?

```python
from forgesight import instrument

@instrument(kind="tool", name="web_search")
def web_search(query: str) -> list[str]: ...

@instrument(kind="agent", version="1.2.0")
async def classify(issue: str) -> str: ...
```

### How do I attach business metadata for cost attribution?

```python
with forgesight.telemetry.agent_run("classifier") as run:
    run.set_metadata(team="platform", repo="agentforge")   # → every child span
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.set_metadata(prompt_variant="B")              # → this LLM span only
```

### How do I capture telemetry in tests?

```python
from forgesight import configure, telemetry, InMemoryExporter

mem = InMemoryExporter()
configure(exporters=[mem])
with telemetry.agent_run("t") as run:
    with run.tool_call("search"):
        ...
assert [r.kind for r in mem.records]   # AGENT + TOOL records captured
```

### Does telemetry block my agent or fail my run?

No. Scope enter/exit is CPU-only (build record → interceptors → hand off); a
failing or slow exporter is isolated and never propagates (P6). An exception inside
a scope is recorded (`status=ERROR`, `RUN_FAILED` emitted) and **re-raised** — the
SDK never swallows your exception (FR-7).
