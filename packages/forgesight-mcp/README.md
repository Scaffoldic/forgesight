# forgesight-mcp

MCP instrumentation for [ForgeSight](https://github.com/Scaffoldic/forgesight). Turns every
[Model Context Protocol](https://modelcontextprotocol.io) `tools/call` / `tools/list` /
`prompts/get` / `resources/read` into a correctly-mapped span — with W3C trace propagation
across the transport, so a `pr-reviewer → github-mcp → internal-api-mcp` chain is **one
trace**.

```bash
pip install forgesight-mcp
```

```python
# client side — an agent calling an MCP server
import forgesight
from forgesight_mcp import instrument_mcp_client
from mcp import ClientSession

forgesight.configure()

async with ClientSession(read, write) as session:
    instrument_mcp_client(session)          # one line; wraps the transport
    await session.initialize()
    result = await session.call_tool("get_diff", {"pr": 42})   # execute_tool get_diff
```

```python
# server side — a tool/resource server
import forgesight
from forgesight_mcp import instrument_mcp_server
from mcp.server import Server

forgesight.configure()
server = Server("github-mcp")
instrument_mcp_server(server)               # extracts traceparent, opens child spans
```

## What you get

- **One span per request**, mapped per the OTel MCP conventions. A `tools/call` is the
  *single* span carrying both `mcp.method.name = tools/call` **and**
  `gen_ai.operation.name = execute_tool` / `gen_ai.tool.name` — never double-instrumented.
- **Uniform tool telemetry.** A tool reached via MCP and the same tool reached natively both
  land under `gen_ai.tool.name`, so "failure rate of `get_diff`" is one query across both.
- **Traces stitch automatically.** The client injects `traceparent` into the request `_meta`;
  the server extracts it and opens its span as a child of the caller's — same `trace_id`.
- **Metrics for free.** Each call feeds `mcp.client.operation.duration` and the derived
  `forgesight.mcp.invocations_total` (server / method / tool / status).
- **Secure by default (P7).** `tools/call` arguments and results are captured **only** when
  `capture_content` resolves true; the redaction interceptor still runs.

## Idempotent + reversible

`instrument_mcp_client` / `instrument_mcp_server` are idempotent (re-instrumenting is a
no-op) and reversible (`uninstrument_mcp_client` / `uninstrument_mcp_server` restore the
originals). Auto-instrument new sessions/servers at `configure()` via the `install()` entry
point:

```yaml
# forgesight.yaml
integrations:
  mcp:
    enabled: true
    auto_instrument: true
    capture_content: false    # P7 default
```

## License

Apache-2.0
