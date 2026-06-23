# Playbook 01 — Install ForgeSight

> Goal: get `forgesight` plus exactly the backends/integrations you need into your project.

## The 30-second version

```bash
pip install forgesight                 # core + batteries-included facade
pip install "forgesight[otel]"         # add one backend (OTLP -> any OTel platform)
```

```python
import forgesight
forgesight.configure(service_name="my-agent")   # zero-config -> console/in-memory in dev
```

That's enough to emit telemetry to the console. Everything else is choosing extras.

## What `pip install forgesight` gives you

The facade pulls in the three core tiers (ADR-0002):

- `forgesight-api` — locked contracts (domain model + SPIs), no I/O, no vendor SDKs
- `forgesight-core` — the runtime: context propagation, span tree, async export, cost, metrics
- `forgesight` — the facade you import: `configure()`, `telemetry`, `@instrument`

No backend is included by default — that's the point. You add backends as **extras**.

## Add backends & integrations as extras

```bash
pip install "forgesight[otel]"                    # one
pip install "forgesight[otel,langfuse,datadog]"   # several
pip install "forgesight[all]"                     # everything except the heavy CrewAI tree
```

| Extra | Pulls | For |
|---|---|---|
| `otel` | `forgesight-otel` | any OTLP backend (Honeycomb, Jaeger, Tempo, New Relic, Phoenix, …) |
| `langfuse` | `forgesight-langfuse` | Langfuse observations + cost |
| `datadog` | `forgesight-datadog` | Datadog APM + cost metric |
| `clickhouse` | `forgesight-clickhouse` | columnar analytics |
| `prometheus` | `forgesight-prometheus` | `/metrics` + push-gateway |
| `mcp` | `forgesight-mcp` | MCP client/server spans + W3C propagation |
| `fastapi` | `forgesight-fastapi` | request↔run correlation + flush-on-deploy |
| `github` | `forgesight-github` | GitHub Actions run↔commit/PR/job + cost summary |
| `governance` | `forgesight-governance` | budgets, policy, kill-switch |
| `eval` | `forgesight-eval` | eval scores + human feedback |
| `registry` | `forgesight-registry` | agent registry, ownership & chargeback |
| `adapters-langgraph` | `forgesight-adapters-langgraph` | auto-instrument LangGraph/LangChain |
| `adapters-crewai` | `forgesight-adapters-crewai[crewai]` | auto-instrument CrewAI (pulls CrewAI) |
| `all` | every package above except `adapters-crewai` | the full toolkit |

Each integration is also a standalone distribution if you'd rather pin it directly:
`pip install forgesight-otel`.

## Pin it in your project

**requirements.txt**

```
forgesight[otel,governance]==0.1.0
```

**pyproject.toml**

```toml
[project]
dependencies = [
  "forgesight[otel,governance]~=0.1.0",
]
```

## Contributor / dev setup

Regular users just `pip install forgesight` (above). To hack on ForgeSight itself, work
from a checkout:

```bash
git clone https://github.com/Scaffoldic/forgesight.git && cd forgesight
uv sync --all-packages          # installs all 17 packages in editable mode
uv run pytest                   # sanity-check the workspace
```

To layer locally-built wheels into a *different* project's environment, build them and
install with `--find-links` (see the cross-workspace recipe in the AgentForge example README).

## Verify

```python
import forgesight
forgesight.configure(service_name="smoke-test")
from forgesight import telemetry
with telemetry.agent_run("smoke") as run:
    with run.tool_call("noop"):
        pass
# a span prints to the console (ConsoleExporter is the dev default)
```

Python 3.11, 3.12, and 3.13 are supported.

## Next

→ [02 — Instrument your agent](./02-instrument-your-agent.md)
