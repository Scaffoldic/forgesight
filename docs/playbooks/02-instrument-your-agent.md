# Playbook 02 — Instrument your agent

> Goal: emit a run with nested LLM/tool/step spans, token usage, and derived cost — in any
> agent, sync or async, with no framework lock-in.

There are three ways to instrument, from most explicit to zero-touch. Pick one (or mix):

1. **Manual scopes** — full control, works anywhere.
2. **Decorator** — annotate functions you already have.
3. **Framework adapter** — don't touch the agent at all.

## 0. Configure once at startup

```python
import forgesight

forgesight.configure(
    service_name="my-agent",
    exporters=["otel"],                                   # pick a backend by name
    exporter_config={"otel": {"endpoint": "http://localhost:4317"}},
)
```

Zero-config (`forgesight.configure()`) emits to the console/in-memory — perfect for dev and
tests. Config layers **file → env → kwargs** (last wins), so the same code reads
`forgesight.yaml` / `FORGESIGHT_*` in production. See
[04 — Ship to a backend](./04-ship-to-a-backend.md).

## 1. Manual scopes

Everything nests automatically via context propagation — child scopes attach to whatever run
is active on the current (async) context.

```python
from forgesight import telemetry

async def run_agent(task: str) -> str:
    with telemetry.agent_run("my-agent", version="1.0.0", metadata={"team": "growth"}) as run:
        with run.step("plan"):
            plan = make_plan(task)

        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            resp = await client.messages.create(...)
            call.record_usage(                      # tokens -> cost is derived for you
                input=resp.usage.input_tokens,
                output=resp.usage.output_tokens,
                cache_read=resp.usage.cache_read_input_tokens,
            )
            call.record_response(finish_reasons=["end_turn"])
            # call.set_cost(0.0123)   # only if you want to override the derived cost

        with run.tool_call("web_search", tool_type="function"):
            results = search(plan.query)

        return summarize(results)
```

Scope cheatsheet (all are sync **and** async context managers):

| Scope | Open with | Key methods |
|---|---|---|
| Run (root) | `telemetry.agent_run(name, version=…, metadata=…)` | `step`, `llm_call`, `tool_call`, `mcp_call`, `set_metadata` |
| Workflow | `telemetry.workflow_run(name, metadata=…)` | same children |
| Step | `run.step(name)` | nest further |
| LLM call | `run.llm_call(provider, model)` | `record_usage(input,output,cache_read,cache_creation,reasoning)`, `record_response`, `record_params`, `set_cost` |
| Tool call | `run.tool_call(name, tool_type=…, call_id=…)` | `set_metadata` |
| MCP call | `run.mcp_call(server, method, tool=…)` | `set_metadata` |

Get the active run anywhere with `forgesight.current_run()`.

## 2. Decorator

```python
from forgesight import instrument

@instrument(kind="agent", name="my-agent", version="1.0.0")
async def run_agent(task: str) -> str:
    ...

@instrument(kind="tool")            # a tool span named after the function
def web_search(q: str) -> list[str]:
    ...

@instrument(kind="step", capture_args=True)
def plan(task: str): ...
```

`kind` is one of `agent`, `step`, `tool`. LLM/MCP/workflow spans need call-time
provider/model/server, so open those with the scope API above.

## 3. Framework adapter (zero agent change)

On LangGraph/LangChain or CrewAI, instrument the framework instead of your code:

```python
import forgesight
from forgesight_adapters_langgraph import LangGraphAdapter

forgesight.configure(exporters=["otel"])
LangGraphAdapter().instrument()                  # the unchanged graph is now traced
result = await my_compiled_graph.ainvoke({"task": "..."})
```

See the [framework adapters runbook](../runbooks/framework-adapters.md).

## Capturing prompt/response content (opt-in)

Content is **never** captured unless you ask (P7 — secure by default):

```python
forgesight.configure(capture_content=True)        # or FORGESIGHT_CAPTURE_CONTENT=true
```

A redaction interceptor runs before export. Leave it off unless you need it.

## Flush before exit

Export is async on a background worker. Short-lived processes (scripts, CLIs, serverless)
should flush so nothing is lost:

```python
forgesight.get_runtime().shutdown()       # flushes, then stops the worker
```

Long-lived services flush on shutdown via the integration (e.g. FastAPI's `sdk_lifespan`).
In tests, set `sync_export=True` to skip the worker entirely. See the
[export pipeline runbook](../runbooks/export-pipeline.md).

## Next

→ [03 — Run locally with Docker](./03-run-locally-with-docker.md) to *see* the data.
