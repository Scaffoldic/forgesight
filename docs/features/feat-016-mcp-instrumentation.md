# feat-016: MCP instrumentation

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-016 |
| **Title** | MCP instrumentation — client + server spans, `mcp.*` conventions, `tools/call` as `execute_tool` |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-mcp` |
| **Depends on** | feat-002 (+ feat-001) |
| **Blocks** | none |

---

## 1. Why this feature

The Model Context Protocol is becoming the default way agents reach tools,
prompts, and resources — an agent calls an MCP server, that server may itself
be an agent calling further MCP servers. The moment an agent's capability moves
behind MCP, the SDK goes blind: a `tools/call` that takes four seconds and
fails shows up as a gap in the trace, not a span. The questions an operator
asks during an incident — *which MCP server was slow, which `tools/call`
errored, did the trace even cross the transport* — have no answer.

Concrete scenarios this hits today:

- A `pr-reviewer` agent calls a `github` MCP server's `tools/call` for
  `get_diff`. The call hangs. Without instrumentation the run trace stops at
  the agent and resumes after the timeout — no span, no duration, no
  `error.type`. The on-call engineer cannot tell the MCP hop from a slow LLM.
- The same tool is reachable two ways — as a native function tool in one agent
  and via MCP in another. Their telemetry looks completely different
  (`execute_tool` span vs. nothing), so you cannot compare "how often does
  `get_diff` fail" across the fleet.
- An MCP server is itself instrumented and an MCP client is itself
  instrumented, but the trace does not stitch: the server opens a brand-new
  root trace because no `traceparent` rode across the transport. Two
  disconnected traces for one logical call.

## 2. Why this belongs in the SDK (vs each team wiring it by hand)

- **The MCP↔OTel mapping is fiddly and easy to get subtly wrong.** The OTel MCP
  conventions say a `tools/call` is *both* an MCP method (`mcp.method.name =
  tools/call`) *and* a tool execution (`gen_ai.operation.name = execute_tool`,
  `gen_ai.tool.name = <tool>`), and that you must **not** also emit a separate
  `execute_tool` span on top of it. Every team that re-derives this either
  double-instruments (two spans per tool call, inflated metrics) or under-maps
  (an MCP call that never unifies with native tool calls). Owning it once means
  one correct mapping for the whole fleet.
- **Uniformity is the whole point.** FR-2 and FR-4 only pay off if an MCP
  `tools/call` and a native `tool_call` land as the *same shape* of span and
  feed the *same* `tool_invocations_total` / failure metrics. That uniformity
  is a contract, not a per-team convention; it has to live below the agent.
- **W3C propagation over the MCP transport is a cross-cutting invariant.** The
  client must inject `traceparent`/`tracestate` into the outgoing MCP request
  and the server must extract them to continue the trace. If each team wires
  this, half will forget `tracestate`, sampling decisions won't propagate, and
  multi-hop MCP traces will fragment. The SDK enforces one propagator on both
  sides.
- **Secure-by-default (P7) must hold at the MCP boundary too.** Tool arguments
  and results are exactly the high-risk content the SDK gates by default. A
  hand-rolled MCP wrapper is precisely where someone logs the full arg payload
  "just for debugging" and ships a PII leak. The content gate has to be in the
  shared instrumentation.
- **Anti-pattern if left to teams:** every MCP-using agent grows a bespoke
  wrapper, no two emit the same `mcp.*` attributes, metrics aren't comparable,
  and the propagation story rots first — the classic observability decay
  requirements §1.1 calls out.

This is framework-agnostic (P3): it wraps the *MCP transport*, not any agent
framework, and ships as its own package wrapping one integration target
(P1/P2) — never added to core.

## 3. How consuming agents/teams benefit

- **Before:** an agent author wraps every `session.call_tool(...)` in a
  hand-written `try/except`, manually opens a span, guesses at attribute names,
  forgets propagation, and double-counts when they later also wrap the tool.
  ~30–50 lines per MCP integration, wrong half the time.
  **After:** `pip install forgesight-mcp`, one `instrument_mcp_client(session)`
  call (or auto-instrument on `configure()`), and every `tools/call` /
  `tools/list` / `prompts/get` is a correctly-mapped span — zero changes to the
  agent's tool-calling code.
- **Uniform tool telemetry for free.** A tool reachable via MCP and the same
  tool reachable natively both show up under `gen_ai.tool.name` with
  `gen_ai.operation.name = execute_tool`. "Failure rate of `get_diff`" is one
  query across both. No per-team reconciliation.
- **Distributed traces stitch automatically.** Client injects, server extracts;
  a `pr-reviewer → github-mcp → internal-api-mcp` chain is one trace with one
  `trace_id`. The author writes no propagation code.
- **One metric, fleet-wide.** `mcp.client.operation.duration` plus the derived
  `mcp_invocations_total` (FR-4: server / method / tool / request-count /
  duration / success-rate) land for every MCP-using agent identically, so the
  platform team gets a per-server reliability dashboard without asking any
  agent author to do anything.
- **Server authors get the same deal.** An MCP server built by one team is
  instrumented by installing the same package; its spans become the children of
  whatever client called it, so a server owner sees their `tools/call`
  latency in the same trace the caller sees.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — MCP CLIENT side (an agent calling an MCP server)
import forgesight
from forgesight_mcp import instrument_mcp_client
from mcp import ClientSession

forgesight.configure()                 # feat-010 bootstrap

async with ClientSession(read, write) as session:
    instrument_mcp_client(session)         # one line; wraps the transport
    await session.initialize()

    # Unchanged agent code — every call below is now a span:
    tools  = await session.list_tools()                       # mcp.method.name=tools/list
    result = await session.call_tool("get_diff", {"pr": 42})  # execute_tool get_diff
```

```python
# python — MCP SERVER side (a tool/resource server)
import forgesight
from forgesight_mcp import instrument_mcp_server
from mcp.server import Server

forgesight.configure()
server = Server("github-mcp")
instrument_mcp_server(server)              # extracts traceparent, opens server spans

# Unchanged @server.call_tool() / @server.list_tools() handlers.
```

```typescript
// typescript — client side
import { configure } from '@agentforge/sdk';
import { instrumentMcpClient } from '@agentforge/sdk-mcp';
import { Client } from '@modelcontextprotocol/sdk/client';

configure();
const session = new Client(/* ... */);
instrumentMcpClient(session);
const result = await session.callTool({ name: 'get_diff', arguments: { pr: 42 } });
```

Auto-instrumentation: when `forgesight-mcp` is installed and entry-point
auto-load is on (feat-010), `configure()` patches the MCP client/server
transports so even the `instrument_*` line is optional. The explicit call stays
supported for full control and for hosts that disable auto-load.

### 4.2 Public API / contract

```python
# forgesight_mcp/__init__.py

def instrument_mcp_client(
    session: "ClientSession",
    *,
    capture_content: bool | None = None,     # None ⇒ inherit global (P7, default off)
    methods: "Sequence[str] | None" = None,  # None ⇒ all known methods
) -> "ClientSession":
    """Wrap an MCP client session: span per request, W3C inject, mcp.* attrs.

    Idempotent — instrumenting an already-instrumented session is a no-op.
    Returns the same session for chaining.
    """

def instrument_mcp_server(
    server: "Server",
    *,
    capture_content: bool | None = None,
    methods: "Sequence[str] | None" = None,
) -> "Server":
    """Wrap an MCP server: extract incoming traceparent, span per handled request.

    Idempotent. Returns the same server.
    """

def uninstrument_mcp_client(session: "ClientSession") -> None: ...
def uninstrument_mcp_server(server: "Server") -> None: ...
```

```typescript
// @agentforge/sdk-mcp
export function instrumentMcpClient(session: Client, opts?: McpInstrumentOptions): Client;
export function instrumentMcpServer(server: Server, opts?: McpInstrumentOptions): Server;
export function uninstrumentMcpClient(session: Client): void;
export function uninstrumentMcpServer(server: Server): void;

export interface McpInstrumentOptions {
  captureContent?: boolean;   // default: inherit global (off)
  methods?: string[];         // default: all known methods
}
```

Stability: the four functions + the options object are the public surface and
are **stable** for 0.2. The exact set of auto-instrumented methods and the
internal span builder are experimental and may grow.

### 4.3 Internal mechanics

`instrument_mcp_client` wraps the session's request-send path. For each
outgoing JSON-RPC request it consults the MCP method → SDK-call mapping, opens
the right span via the feat-002 runtime, injects W3C context, sends, then
closes the span with status/duration. Spans are CLIENT kind (per the otel
mapping §4.2).

```
client.call_tool("get_diff", {...})
   │  open span — operation=execute_tool, name="tools/call get_diff"
   │     mcp.method.name=tools/call · mcp.session.id=… · mcp.protocol.version=…
   │     gen_ai.operation.name=execute_tool · gen_ai.tool.name=get_diff
   │  inject traceparent + tracestate into the JSON-RPC request _meta
   ▼ ───────────────────── MCP transport ─────────────────────
server (instrumented)
   │  extract traceparent + tracestate ⇒ parent context
   │  open server span as CHILD of the client's span (same trace_id)
   │  run the @call_tool handler
   │  CallToolResult.isError ⇒ error.type=tool_error, span ERROR
   ▼  close server span (duration, status)
client closes span ⇒ records mcp.client.operation.duration
```

**Method → mapping** (otel mapping §4.2):

| MCP method | operation.name | Span name | Key attrs |
|---|---|---|---|
| `tools/call` | `execute_tool` | `tools/call {tool}` | `mcp.method.name`, `gen_ai.tool.name` |
| `tools/list` | *(unset)* | `tools/list` | `mcp.method.name` |
| `prompts/get` | *(unset)* | `prompts/get` | `mcp.method.name`, prompt name |
| `prompts/list` | *(unset)* | `prompts/list` | `mcp.method.name` |
| `resources/read` | *(unset)* | `resources/read` | `mcp.method.name`, `mcp.resource.uri` |
| `resources/list` | *(unset)* | `resources/list` | `mcp.method.name` |

**No double-instrumentation (load-bearing).** When `tools/call` is the MCP
method, this instrumentation emits the *single* span that carries both the
`mcp.*` attributes and `gen_ai.operation.name = execute_tool` /
`gen_ai.tool.name`. The SDK does **not** additionally open a native
`execute_tool` span for the same call (otel mapping §4.3). An agent framework
adapter (feat-019) that observes the same MCP call must defer to the MCP span,
not wrap it again — the runtime guards against re-entrant tool-call spans for an
in-flight MCP `tools/call`.

**Attributes set:** `mcp.method.name`, `mcp.session.id`,
`mcp.protocol.version` (from the `initialize` handshake), and
`mcp.resource.uri` for resource methods. On `tools/call`:
`gen_ai.operation.name = execute_tool` and `gen_ai.tool.name`. On error — a
raised exception or `CallToolResult.isError` — set `error.type` (`tool_error`
for `isError`) and span status (FR-7).

**Content gating (P7).** Arguments (`gen_ai.tool.call.arguments`) and results
(`gen_ai.tool.call.result`) are captured **only** when `capture_content`
resolves true; the gate is enforced before the record reaches the pipeline, and
the redaction interceptor (feat-008) still runs.

**Metric.** Each client call records `mcp.client.operation.duration` (Histogram,
`s`, duration buckets per otel mapping §4.4) attributed by
`gen_ai.operation.name`, `gen_ai.provider.name` (the MCP server), and
`error.type` on failure. The SDK's product metric `mcp_invocations_total`
(FR-4/FR-6) is derived from these records (feat-005), namespaced `agentforge.*`,
carrying server / method / tool / status so request-count and success-rate are
queryable.

### 4.4 Module packaging

`forgesight-mcp` is its own integration package wrapping exactly one
target — the MCP transport — and is **never** added to core (P1/P3). It depends
on `forgesight-core` + the `mcp` SDK (its single vendor dependency); core
never gains an MCP dependency.

```bash
pip install forgesight-mcp
```

```yaml
# forgesight.yaml
integrations:
  mcp:
    enabled: true
    auto_instrument: true        # patch client/server transports at configure()
    capture_content: false       # P7 default
```

Entry-point registration: `forgesight.integrations` →
`mcp = forgesight_mcp:install` so `configure()` discovers and wires it when
`auto_instrument` is on.

### 4.5 Configuration

| Key | Env | Default | Meaning |
|---|---|---|---|
| `integrations.mcp.enabled` | `FORGESIGHT_MCP_ENABLED` | `true` (when installed) | Master switch. |
| `integrations.mcp.auto_instrument` | `FORGESIGHT_MCP_AUTO` | `true` | Patch transports at `configure()` vs. require explicit `instrument_*`. |
| `integrations.mcp.methods` | `FORGESIGHT_MCP_METHODS` | all known | Which methods to instrument. Restrict to e.g. `["tools/call"]` to spans only tool calls. |
| `integrations.mcp.capture_content` | `FORGESIGHT_MCP_CAPTURE_CONTENT` | `false` | Capture `tools/call` args + results. Off by default (P7); honours the global content gate when unset. |

Validation: unknown method names in `methods` warn and are ignored (forward-
compat with new MCP methods); `capture_content: true` is logged at INFO once so
content capture is never silently on.

## 5. Plug-and-play & upgrade story

Add later with `pip install forgesight-mcp` + the YAML block; no agent-code
change (P2). Remove by uninstalling — the runtime degrades to no MCP spans, the
agent keeps working. Minor upgrades may add newly-standardised `mcp.*`
attributes or methods behind defaults; the four public functions keep their
signatures (P5). Re-pinning the underlying GenAI/MCP semconv revision is a
feat-004 concern and invisible here.

## 6. Cross-language parity

Identical: span names, the method→operation mapping, `mcp.*` and `gen_ai.*`
attributes, the `mcp.client.operation.duration` metric, W3C propagation, the
no-double-instrument rule, content-gate default. Differs: Python wraps
`ClientSession`/`Server` from the `mcp` package via `contextvars`; TS wraps the
`@modelcontextprotocol/sdk` `Client`/`Server` via `AsyncLocalStorage`. No
method deferred in either language for 0.2.

## 7. Test strategy

- **Unit:** method→span mapping table; `tools/call` produces one span carrying
  both `mcp.*` and `execute_tool`/`gen_ai.tool.name`; idempotent
  re-instrumentation; `uninstrument_*` restores original behaviour.
- **No-double-instrument:** assert exactly one span per `tools/call` even when a
  framework adapter is also active.
- **Propagation:** client-injected `traceparent`+`tracestate` extracted
  server-side yields a child span with the same `trace_id` across a process
  boundary (in-memory transport in tests).
- **Content gate (P7):** args/results absent by default; present only with
  `capture_content`; redaction interceptor still applies.
- **Error:** raised handler exception and `CallToolResult.isError` both set
  `error.type` + span ERROR.
- **Metric:** `mcp.client.operation.duration` recorded with the right
  attributes; `mcp_invocations_total` derivable with server/method/tool/status.
- **Conformance:** runs the feat-011 exporter/span-tree assertions against the
  in-memory exporter.
- **Example agent:** an MCP client agent + an MCP server, end-to-end, one trace.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| MCP SDK internals change shape | Wrap the public session/server API, not internals; pin a tested `mcp` range; conformance catches drift. |
| `_meta` not a stable carrier for `traceparent` across MCP transports | Use the MCP request `_meta` as the propagation carrier per current guidance; isolate the carrier in one place to re-pin if the spec moves. |
| Double-instrumentation via a framework adapter also wrapping the call | Runtime guards re-entrant `execute_tool` spans for an in-flight MCP `tools/call`; documented adapter contract: defer to the MCP span. |
| `mcp.protocol.version` unknown before `initialize` | Captured from the handshake; absent (not fabricated) on pre-init calls (spec: don't fabricate). |
| Content capture leaking PII | Off by default; gated before pipeline; redaction interceptor first; INFO log when enabled. |

## 9. Out of scope

- **Instrumenting MCP transports themselves** (stdio/SSE/streamable-HTTP socket
  internals) beyond request/response spans — that is OTel auto-instrumentation
  territory; we compose with it, not replace it.
- **MCP sampling / elicitation flows** as first-class spans — recorded as
  generic methods for now; first-class mapping is a follow-up if demand
  justifies.
- **A dedicated MCP backend/dashboard** — emit only; visualisation is the
  backend's job (requirements §11).
- **Replacing or orchestrating MCP servers** — the SDK observes, it does not
  proxy or route (requirements §11).

## 10. References

- [`../requirements.md`](../requirements.md) — FR-4 (MCP tracking), FR-2, FR-6
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2–4.5 (MCP mapping, `tools/call` as `execute_tool`, no double-instrument, W3C propagation, `mcp.client.operation.duration`)
- [`../design/architecture.md`](../design/architecture.md) §4 (`MCPCall`), §5 (packaging)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P3, P4, P7
- feat-001 (`MCPCall` model), feat-002 (runtime / instrumentation API), feat-004 (semconv mapping), feat-005 (metrics)
- OpenTelemetry MCP semconv: <https://github.com/open-telemetry/semantic-conventions-genai>
