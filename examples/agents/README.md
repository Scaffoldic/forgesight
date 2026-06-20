# ForgeSight agent examples

Three self-contained agents, each a **real AWS Bedrock** call instrumented with ForgeSight and
exporting end-to-end to the local stack — traces to **Jaeger**, metrics to **Prometheus →
Grafana**, plus the tamper-evident **audit trail** (feat-023) and **attributed-cost metrics**
(feat-026).

| Example | Pattern it shows |
|---|---|
| [`react_agent.py`](./react_agent.py) | ReAct loop — `run → step(iteration) → llm_call`/`tool_call`, multiple LLM calls, cost accumulation |
| [`rag_agent.py`](./rag_agent.py) | RAG — a `retrieve` step (vector-search tool) feeding a `generate` step (LLM) |
| [`multi_agent.py`](./multi_agent.py) | Multi-agent — a `supervisor` run delegating to nested `researcher`/`writer` sub-agent runs |

## Prerequisites

- The stack up: `docker compose up -d` (from the repo root).
- AWS credentials with Bedrock access in `us-east-1` (the examples call
  `global.anthropic.claude-haiku-4-5-20251001-v1:0`).
- `boto3` available: `uv pip install boto3`.

## Run

```bash
uv run --no-sync python -m examples.agents.react_agent
uv run --no-sync python -m examples.agents.rag_agent
uv run --no-sync python -m examples.agents.multi_agent
```

Each prints the model's answer, then where everything landed:

- **Jaeger** — http://localhost:16686/search?service=react-agent (swap the service name)
- **Grafana** — http://localhost:3000 → dashboard *ForgeSight — agent telemetry*
- **Audit** — `/tmp/forgesight-*-audit.jsonl` (hash-chained; the script verifies it intact)

### Seeing real totals in Grafana

Each example above is a **short-lived process**: it serves Prometheus metrics on `:9464` for
only a few seconds before exiting, and its counters reset each run — so Prometheus (a *pull*
system, scraping every 5s) rarely captures them and the dashboard under-counts. Traces in
Jaeger are always complete (they're pushed per run); the *pull metrics* just need a long-lived
target. Use the combined runner, which configures once, runs all the agents, and keeps `:9464`
up long enough to be scraped:

```bash
uv run --no-sync python -m examples.agents.demo_all
```

Open Grafana while it's alive — runs/cost/tokens then reflect all **5** agent runs
(react + rag + supervisor/researcher/writer). Traces land under service `forgesight-demo`.

> Cost is stamped via `set_cost()` from Bedrock's real token counts (the built-in pricing
> table doesn't price Bedrock model ids yet). The shared setup lives in
> [`_demo.py`](./_demo.py); copy it into your own agent and change the `agent_run` body.
