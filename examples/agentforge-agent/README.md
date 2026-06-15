# Example: instrumenting an AgentForge agent with ForgeSight

A complete, **offline, end-to-end** example: scaffold an [AgentForge](https://github.com/Scaffoldic/agentforge-py)
agent following its "Build your own agent" guide, instrument it with ForgeSight, run it with
**no API key and no network**, and validate that the telemetry was captured and exported.

It answers the integration question concretely: *AgentForge runs the agent; ForgeSight
records what it did and what it cost, correlated by run id, and exports it anywhere — with
no change to the agent's code.*

## What's here

| File | Role |
|---|---|
| `agent.py` | The AgentForge agent (a `@tool` + `Agent(...)`, driven offline by a scripted `FakeLLMClient`) + ForgeSight `configure()` + validation. |
| `forgesight_bridge.py` | The integration point: replays an AgentForge `RunResult` into a ForgeSight `agent_run` trace. A hand-rolled stand-in for the first-party `forgesight-adapters-agentforge` adapter. |

## What it proves

Running `agent.py` prints AgentForge's result, then ForgeSight's captured trace, and asserts:

```
records : 6  (agent=1 step=2 llm=2 tool=1)
tool spans        : ['lookup_order']
cost (ForgeSight) : $0.0055  == AgentForge $0.0055
✅ end-to-end OK
```

- The AgentForge ReAct loop (`think` → `act` → `observe`) maps to a clean ForgeSight trace:
  `agent_run → step(iteration-N) → [llm_call, tool_call]`.
- Token usage and **cost** flow through unchanged (ForgeSight total == AgentForge's
  `result.cost_usd`).
- The run carries `agentforge.run_id` as correlation metadata + your business metadata
  (`team`, `environment`), so it rolls up the same way any ForgeSight agent does.
- Everything shares one `trace_id` and is exported (here to an `InMemoryExporter` we assert
  against, plus a `ConsoleExporter` so you can see it).

## Run it

AgentForge requires **Python 3.13**; ForgeSight supports 3.11–3.13. You need one environment
with **both** installed.

### Once both are published to PyPI

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install "agentforge-py" forgesight
python agent.py
```

### From this monorepo (what the example was validated with)

ForgeSight and AgentForge are separate `uv` workspaces, so the simplest path is to build
ForgeSight wheels and layer them into AgentForge's environment:

```bash
# 1. Build ForgeSight wheels (run from agents/forgesight/)
for p in forgesight-api forgesight-core forgesight; do
  (cd packages/$p && uv build --wheel --out-dir /tmp/fs-wheels)
done

# 2. Install them into AgentForge's (Python 3.13) workspace env
cd ../../python/agentforge-py
uv pip install --find-links /tmp/fs-wheels forgesight

# 3. Run the example
uv run python ../../agents/forgesight/examples/agentforge-agent/agent.py
```

No API key, no network — the agent loop runs against a scripted `FakeLLMClient`.

## Wiring it into a *real* AgentForge agent

The example uses the offline fake model; a real agent swaps it for a provider
(`Agent(model="anthropic:claude-sonnet-4-5", tools=[...])`) and is otherwise identical. The
ForgeSight side doesn't change: `configure(...)` once, then `instrument_agentforge_run(result, ...)`
after each `agent.run(...)`. Point ForgeSight at a real backend by swapping the exporter —
`exporters=["otlp"]` (or `langfuse`, `datadog`, …) — no code change.

For deep, per-call capture without the post-run replay (e.g. live spans as the loop runs),
the first-party `forgesight-adapters-agentforge` adapter would subscribe to AgentForge's
`on_step` / `on_finish` hooks; this bridge shows the same domain-model mapping it would use.
