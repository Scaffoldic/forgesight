# MCP instrumentation runbook

> Trace every MCP `tools/call` / `prompts/get` / `resources/read` as a span and stitch one trace across the client→server transport. **Extra:** `pip install "forgesight[mcp]"` · **Spec:** [feat-016](../features/feat-016-mcp-instrumentation.md)

## What it does

Wraps the public request methods on an MCP `ClientSession` (and the handlers on an MCP `Server`) so each request opens a `forgesight_core.MCPScope` span carrying `mcp.*` attributes. On the client it injects a W3C `traceparent` into the request `_meta`; on the server it extracts that context so the server span continues the caller's trace. A `tools/call` becomes a *single* span that is both the MCP method (`mcp.method.name = tools/call`) and the tool execution (`gen_ai.operation.name = execute_tool`) — never a second span.

## When to use it

- Your agent calls one or more MCP servers (e.g. a `github` MCP server's `tools/call`) and you want per-call latency, status, and trace correlation.
- You own an MCP server and want your `tools/call` latency to appear in the same trace the caller sees.
- You want W3C propagation across the MCP transport without writing any propagation code.

## Install

```bash
pip install "forgesight[mcp]"        # facade extra
# or the standalone package:
pip install forgesight-mcp           # depends on forgesight-core + mcp>=1
```

## Set up

Manual, explicit instrumentation of a session you already created:

```python
from mcp import ClientSession
from forgesight_mcp import instrument_mcp_client

session = ClientSession(read, write)
instrument_mcp_client(session)        # idempotent; returns the same session for chaining
await session.initialize()
result = await session.call_tool("get_diff", arguments={...})   # one mcp.* + execute_tool span
```

Server side, wrap the handler registry on a `Server`:

```python
from mcp.server import Server
from forgesight_mcp import instrument_mcp_server

server = Server("my-tools")
instrument_mcp_server(server)         # extracts incoming traceparent; span per handled request
```

Auto-instrument path — let the SDK patch new sessions/servers at creation via the
`forgesight.integrations` entry point (group `forgesight.integrations`, name **`mcp`** →
`forgesight_mcp:install`). With the `integrations.mcp` config block enabled and
`auto_instrument` on, `install()` patches `ClientSession.__init__` / `Server.__init__` so
every new instance is instrumented:

```python
from forgesight_mcp import install, uninstall

install({"enabled": True, "auto_instrument": True})   # idempotent; honours methods / capture_content
# ... run your agent; every ClientSession / Server created is now instrumented ...
uninstall()                                            # restores the original constructors
```

W3C propagation note: the carrier is the MCP request `_meta` map. The client writes
`traceparent` (version-`00`, sampled flag `01`) via `inject_traceparent`; the server parses it
with `extract_context`. A missing or malformed `traceparent` degrades to a *new local trace* —
it never raises and never breaks the call.

Re-entrancy: while a `tools/call` span is open, `in_mcp_tool_call()` returns `True`, so a
framework adapter (feat-019) defers to the MCP span instead of opening a second `execute_tool`
span (no double-instrument).

## What it emits / correlates

Per request, an `MCPScope` span with `mcp.*` attributes. The methods spanned by default are
`KNOWN_METHODS`:

| MCP method | Client attr wrapped | Notable span metadata |
| --- | --- | --- |
| `tools/call` | `call_tool` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name=<tool>`; with capture on, `gen_ai.tool.call.arguments` + `gen_ai.tool.call.result` |
| `tools/list` | `list_tools` | — |
| `prompts/get` | `get_prompt` | `mcp.prompt.name` |
| `prompts/list` | `list_prompts` | — |
| `resources/read` | `read_resource` | `mcp.resource.uri` |
| `resources/list` | `list_resources` | — |

- **Correlation:** the client injects `traceparent` into `_meta`; the server opens its span as a
  child of the client's span — one `trace_id` end to end.
- **Errors:** a `tools/call` whose `CallToolResult.isError` is true is recorded as
  `error.type = tool_error` on both client and server (the class name is the load-bearing
  semconv value).
- **Protocol version:** captured from `initialize()` and stamped on subsequent spans.

## Operate it

Runtime requirements: the `mcp` package (`mcp>=1`) and a configured ForgeSight runtime
(`configure()` / your exporter). Content capture (`tools/call` arguments + results) is **opt-in**
— pass `capture_content=True`, set it in the `integrations.mcp` config block, or rely on the
global gate; when it turns on, the integration logs an INFO once so it is never silently on.

Verify:

1. Instrument a client session, call a tool, and point your exporter at a collector (see
   `forgesight[otel]` → Jaeger via the root [`docker-compose.yml`](../../docker-compose.yml)).
2. In Jaeger (http://localhost:16686) look for a span with `mcp.method.name = tools/call` and
   `gen_ai.operation.name = execute_tool` — exactly one span, not two.
3. With both client and server instrumented, confirm the server span is a child of the client
   span under the same `trace_id`.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| No MCP spans appear | Session/server never instrumented, or auto-instrument disabled | Call `instrument_mcp_client`/`instrument_mcp_server`, or `install({"auto_instrument": True})` |
| Server span is a new root, not a child | Incoming `traceparent` missing/malformed in `_meta`, or the client wasn't instrumented | Instrument the client; a bad header intentionally degrades to a local root (never raises) |
| Two spans for one `tools/call` | A framework adapter also wrapped the tool | Rely on `in_mcp_tool_call()` deferral; don't double-instrument the same call |
| `WARN: ignoring unknown MCP method` | A `methods=[...]` entry isn't in `KNOWN_METHODS` | Use only known method strings (e.g. `tools/call`); unknown names are dropped for forward-compat |
| Arguments/results missing from spans | Content capture is off (secure by default) | Enable `capture_content` explicitly or via config |
| Export failures don't surface | By design: export is non-blocking and `export()` returns failure, never raises | Check exporter logs; instrumentation never throws into your call path |

## Reference

- Feature spec: [feat-016 MCP instrumentation](../features/feat-016-mcp-instrumentation.md)
- Package: [`packages/forgesight-mcp`](../../packages/forgesight-mcp)
- Playbook: [Install ForgeSight](../playbooks/01-install.md)
- Playbook: [Instrument your agent](../playbooks/02-instrument-your-agent.md)
