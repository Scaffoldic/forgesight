# forgesight-adapters-crewai

The CrewAI adapter for [ForgeSight](https://github.com/Scaffoldic/forgesight). An *unchanged*
CrewAI crew emits a correct ForgeSight span tree — with cost and metrics — by subscribing to
the CrewAI event bus.

```bash
pip install forgesight-adapters-crewai   # alongside your existing crewai install
```

```python
import forgesight
from forgesight_adapters_crewai import CrewAIAdapter

forgesight.configure()
CrewAIAdapter().instrument()             # subscribe to the CrewAI event bus

result = my_crew.kickoff(inputs=...)     # unchanged
```

## What it maps

| CrewAI event | ForgeSight |
|---|---|
| `CrewKickoff*` | `workflow_run` |
| `AgentExecution*` | `agent_run` |
| `Task*` | `step` |
| `LLMCall*` | `llm_call` (+ token usage when the event carries it) |
| `ToolUsage*` | `tool_call` (`execute_tool`) |
| `*Failed` / `*Error` | span ERROR + `error.type` |

Because every adapter targets the *same* domain model, a CrewAI crew and (say) a LangGraph
graph produce comparable `agent_runs_total` / cost views. Cost is derived by the runtime from
the extracted token usage — the adapter never computes cost. When token usage is absent from
an event, the call is still recorded (cost stays null — FR-9).

**No double-instrument.** A tool call already covered by an inner span (an MCP `tools/call`,
feat-016) is deferred — no second `execute_tool`.

## Dependency note

`crewai` itself (a large dependency tree) is **not** re-pinned by this package — you already
have it. It is imported lazily; install it via the `crewai` extra or in your environment.

## License

Apache-2.0
