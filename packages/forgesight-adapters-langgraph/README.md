# forgesight-adapters-langgraph

The LangGraph / LangChain adapter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
An *unchanged* LangGraph graph emits a correct ForgeSight span tree — with cost and metrics —
by subscribing to LangChain's callbacks.

```bash
pip install forgesight-adapters-langgraph
```

```python
import forgesight
from forgesight_adapters_langgraph import LangGraphAdapter

forgesight.configure()
LangGraphAdapter().instrument()          # subscribe to LangChain/LangGraph callbacks

# Unchanged graph — now fully instrumented:
result = await my_compiled_graph.ainvoke({"task": "review PR #42"})
```

## What it maps

| LangChain callback | ForgeSight |
|---|---|
| root chain (graph invoke) | `agent_run` |
| nested chain (graph node) | `step` |
| `on_chat_model_start` / `on_llm_*` | `llm_call` (+ token usage from the `LLMResult`) |
| `on_tool_*` | `tool_call` (`execute_tool`) |
| `*_error` | span ERROR + `error.type` |

Because every adapter targets the *same* domain model, a LangGraph agent and (say) a CrewAI
agent produce comparable `agent_runs_total` / cost views. Cost is derived by the runtime from
the extracted token usage — the adapter never computes cost.

**No double-instrument.** When a tool call is already covered by an inner span (an MCP
`tools/call`, feat-016), the adapter defers and does not open a second `execute_tool`.

## Explicit handler

Auto-subscription uses LangChain's inheritable-callback hook. You can also pass the handler
per-invocation: `graph.ainvoke(..., config={"callbacks": [LangGraphAdapter().handler]})`.

## License

Apache-2.0
