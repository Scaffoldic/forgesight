# Framework adapters runbook

> Auto-instrument a LangGraph or CrewAI agent so the *unchanged* graph/crew emits ForgeSight's domain model. **Extra:** `pip install "forgesight[adapters-langgraph]"` / `pip install "forgesight[adapters-crewai]"` · **Spec:** [feat-019](../features/feat-019-framework-adapters.md)

## What it does

A framework adapter subscribes to a framework's *native* hooks and translates each callback/event into a ForgeSight instrumentation call — with no change to the user's agent code. The LangGraph adapter rides LangChain's callback system; the CrewAI adapter rides the CrewAI event bus. Both target the *same* domain model and span/attribute conventions, so telemetry is comparable across frameworks. The adapters hold no agent state: nesting rides the SDK's `TelemetryContext` (contextvars) via the shared `ScopeBridge`.

## When to use it

- You have a working LangGraph workflow or CrewAI crew and want telemetry to *appear* without hand-wrapping every node / LLM call / tool call.
- You want one comparable `agent_runs_total` / `agent_cost_total` view across multiple frameworks.
- You plan to switch frameworks later and want your dashboards to survive the swap (both adapters emit the same shape).
- You want the option to defer the telemetry decision to a package install rather than a code rewrite.

## Install

```bash
pip install "forgesight[adapters-langgraph]"   # LangGraph / LangChain
pip install "forgesight[adapters-crewai]"      # CrewAI
```

The `adapters-langgraph` extra pulls `forgesight-adapters-langgraph` (which depends on `langchain-core`). The `adapters-crewai` extra resolves to `forgesight-adapters-crewai[crewai]` — i.e. it pulls in the **heavy `crewai` tree** (`crewai>=0.80`) via the adapter's own `[crewai]` sub-extra. If you already manage `crewai` in your environment, you can instead install `forgesight-adapters-crewai` without that sub-extra; the CrewAI SDK is imported lazily, so the adapter package itself stays light.

Each adapter registers under the `forgesight.adapters` entry-point group (`langgraph = forgesight_adapters_langgraph:LangGraphAdapter`, `crewai = forgesight_adapters_crewai:CrewAIAdapter`), so `configure()` can discover and (with auto-instrument on) activate every installed, enabled adapter.

## Set up / Configure

### LangGraph — zero agent-code change

```python
import forgesight
from forgesight_adapters_langgraph import LangGraphAdapter

forgesight.configure()
LangGraphAdapter().instrument()        # subscribe to LangChain/LangGraph callbacks

# Unchanged user code — now fully instrumented:
result = await my_compiled_graph.ainvoke({"task": "review PR #42"})
```

`instrument()` registers the handler (`ForgeSightLangChainHandler`) as an *inheritable* LangChain callback via `register_configure_hook`, so every graph/chain run picks it up — you do not touch your graph definition. You can also attach it explicitly per call via `callbacks=[adapter.handler]`. `instrument()` is idempotent (a second call is a no-op) and `uninstrument()` clears it.

### CrewAI — the equivalent

```python
import forgesight
from forgesight_adapters_crewai import CrewAIAdapter

forgesight.configure()
CrewAIAdapter().instrument()           # subscribe to the CrewAI event bus
result = my_crew.kickoff(inputs=...)   # unchanged
```

`instrument()` registers the translator's (`CrewAIEventTranslator`) handlers on the CrewAI event bus. The CrewAI SDK is imported lazily on subscribe (it is your framework, not ForgeSight's dependency); the bus and event types are injectable for testing.

### `ScopeBridge` and `in_tool_call()`

Frameworks signal work as *start* then *end* callbacks, not `with` blocks. `ScopeBridge` opens the matching SDK scope on start and closes it on end, manually driving the scope's context-manager protocol so nesting rides the runtime's contextvars. It has two addressing modes:

- **keyed** — LangGraph, where every callback carries a `run_id`; the bridge looks the open scope up by that key on the end callback.
- **stacked** — CrewAI, whose event bus carries no run ids; the bridge uses a per-kind LIFO stack matching CrewAI's strictly-nested sequential execution.

`in_tool_call()` is the re-entrancy guard: when an inner span (e.g. an MCP `tools/call`, feat-016) already covers a tool execution, an adapter observing the *same* call checks `in_tool_call()` on its tool-start hook and defers instead of opening a second `execute_tool` span.

### Auto-instrument (config path)

With entry-point auto-instrument on, `forgesight.configure()` activates every installed, enabled adapter — even the explicit `instrument()` call becomes optional:

```yaml
# forgesight.yaml
adapters:
  langgraph: { enabled: true }
  crewai:    { enabled: true }
```

## Behavior

Both adapters translate native hooks into the same domain model (the translation, not the subscription, is the valuable part — it is fully unit-tested by driving the handler/translator methods directly).

**LangGraph** (`ForgeSightLangChainHandler`):

- `on_chain_start` with **no parent** → `RunScope` (the graph invocation is the agent run); a nested chain (a node) → `StepScope`.
- `on_chat_model_start` / `on_llm_start` → `LLMScope` (provider/model pulled from `ls_provider` / `ls_model_name` metadata, falling back to the serialized kwargs).
- `on_tool_start` → `ToolScope` (deferred via `in_tool_call()` when an inner span covers it).
- `on_llm_end` pulls token usage from the `LLMResult` and calls `record_usage(input=, output=)`; cost is derived by the runtime, never by the adapter.
- `*_error` callbacks close the keyed scope with the error → span ERROR.

**CrewAI** (`CrewAIEventTranslator`):

- `CrewKickoff*` → `WorkflowScope`; `AgentExecution*` → `RunScope` (named by the agent's `role`); `Task*` → `StepScope`; `LLMCall*` → `LLMScope`; `ToolUsage*` → `ToolScope`.
- `*Failed` / `*Error` events synthesise a `CrewError` (if no exception object is present) so the scope closes with an error.
- A deferred tool span pushes a no-op `_DeferredScope` to keep the per-kind LIFO stack balanced so the matching end event pops cleanly.

Nesting in both cases maps onto the SDK's contextvars, producing a `workflow → agent_run → step → llm/tool` span tree that is identical in shape across frameworks.

## Operate it

To verify an adapter end-to-end, configure an exporter, instrument, run a real graph/crew, and confirm the span tree appears in your backend.

1. Point ForgeSight at a dev backend (e.g. an OTLP collector / Jaeger) and call `forgesight.configure()`.
2. Call `LangGraphAdapter().instrument()` (or `CrewAIAdapter().instrument()`).
3. Run a tiny graph (one node + one LLM call) or a tiny crew (one agent + one task).
4. Confirm the spans appear: a root `agent_run` (LangGraph) or `workflow → agent_run` (CrewAI), a nested `step` per node/task, and a `CLIENT` LLM span carrying token usage and runtime-derived cost.
5. Call `uninstrument()` and re-run; confirm no new ForgeSight spans are produced (clean unsubscribe). A second `instrument()` before that should have been a no-op.

If token usage is missing from a framework's response, the call still records (cost stays null) — graceful degradation, not an error.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No spans at all | `instrument()` never called and auto-instrument off | Call `LangGraphAdapter().instrument()` / `CrewAIAdapter().instrument()`, or enable `adapters.auto_instrument`. |
| LangGraph spans missing on some runs | Custom `callbacks=` overriding the inheritable hook | Pass `callbacks=[adapter.handler]` explicitly, or let `configure()` register the inheritable hook. |
| CrewAI: `instrument()` raises on import | `crewai` not installed in the environment | Install via the `adapters-crewai` extra (pulls `crewai>=0.80`) or add `crewai` to your env. |
| Tool call appears twice | Two instrumentors over the same MCP-backed tool | This should not happen — the adapter checks `in_tool_call()` and defers to the inner span; if it does, file a bug. |
| LLM spans have `provider=unknown` / `model=unknown` | Framework didn't surface `ls_provider` / `ls_model_name` (or CrewAI event lacked `provider`/`model`) | Cosmetic; the span still records. Upgrade the framework or the adapter for richer metadata. |
| Cost is null on an LLM span | Token usage absent from the framework's response object | Expected graceful degradation (FR-9); the call is still recorded. |
| Backend shows nothing but spans exist locally | Exporter failure | Export is **non-blocking and fault-tolerant** — `export()` returns failure, it never raises and never breaks your agent. Check exporter config / backend reachability; adapters never halt the run on a telemetry failure. |

## Reference

- Spec: [feat-019 — Framework adapters](../features/feat-019-framework-adapters.md)
- Packages: [`packages/forgesight-adapters-langgraph`](../../packages/forgesight-adapters-langgraph), [`packages/forgesight-adapters-crewai`](../../packages/forgesight-adapters-crewai)
- Shared adapter infra: [`packages/forgesight-core/src/forgesight_core/adapters/`](../../packages/forgesight-core/src/forgesight_core/adapters) (`base.py` → `BaseAdapter`, `bridge.py` → `ScopeBridge`, `guard.py` → `in_tool_call`)
- Playbooks: [01 — Install](../playbooks/01-install.md), [02 — Instrument your agent](../playbooks/02-instrument-your-agent.md)
