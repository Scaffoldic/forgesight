# ForgeSight

**Vendor-neutral, OpenTelemetry-first telemetry for AI agents — instrument any agent in
a few lines, then ship traces, metrics, cost, evals, and governance to any backend by
editing one line of config. Never an agent-code change.**

[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11_|_3.12_|_3.13-blue.svg)](#quick-start)
[![CI](https://img.shields.io/badge/CI-ruff_·_mypy--strict_·_pytest_≥90%25-brightgreen.svg)](./.github/workflows/ci.yml)

```python
import forgesight
from forgesight import telemetry

forgesight.configure(exporters=["otlp"])          # pick a backend by name — that's it

with telemetry.agent_run("pr-reviewer", version="2.1.0", metadata={"team": "platform"}) as run:
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        resp = await client.messages.create(...)
        call.record_usage(input=resp.usage.input_tokens, output=resp.usage.output_tokens)
    with run.tool_call("github_get_diff"):
        diff = gh.get_diff(pr)
```

Cost, token usage, trace nesting, metrics, and multi-backend fan-out come for free.
Swap `otlp` → `langfuse`, `datadog`, `clickhouse`, `prometheus` — the agent code never moves.

---

## Why ForgeSight

Agent telemetry today is a pile of bespoke glue: every team re-derives token→cost math,
re-invents `run_id` propagation, and writes a different bridge to Langfuse / Datadog /
their collector. No two agents are comparable, and the bill arrives before anyone can act.

ForgeSight makes telemetry **infrastructure**, not glue:

- **🔌 Vendor-neutral by design.** The core depends on *no* backend or model-provider SDK.
  Backends are packages you install and select by config — Langfuse today, Datadog
  tomorrow, your own OTLP collector next week, with zero code change (P1/P2).
- **📐 OpenTelemetry-first.** The canonical wire format is the OTel GenAI semantic
  conventions, so anything that ingests OTLP (Honeycomb, Jaeger, Tempo, New Relic, Arize
  Phoenix, …) works with **no dedicated package**.
- **🚦 Non-blocking & fault-isolated.** Export runs on a background worker; a backend
  outage is counted and invisible to your agent — telemetry never breaks a run (P6).
- **💰 Cost built in.** Token usage → USD via a pluggable, refreshable pricing table
  (input / output / cached / reasoning / tiered) — the same number everywhere.
- **🔒 Secure by default.** Prompt/response content is never captured unless you opt in,
  and a redaction interceptor runs before export (P7).
- **🧩 Stable contracts.** Four `Protocol` SPIs and an immutable domain model, every one
  covered by a conformance suite; `mypy --strict`, coverage ≥ 90%.

---

## What you can do with it

| Capability | Package | One-liner |
|---|---|---|
| **Instrument runs / LLM / tool / MCP calls** | `forgesight` | `with telemetry.agent_run(...) as run: ...` |
| **Ship to an OTLP collector** (Honeycomb/Jaeger/Tempo/Phoenix/…) | `forgesight-otel` | `exporters=["otlp"]` |
| **Langfuse** observations + cost | `forgesight-langfuse` | `exporters=["langfuse"]` |
| **Datadog** APM + cost metric | `forgesight-datadog` | `exporters=["datadog"]` |
| **ClickHouse** columnar analytics | `forgesight-clickhouse` | `exporters=["clickhouse"]` |
| **Prometheus** `/metrics` + push | `forgesight-prometheus` | `exporters=["prometheus"]` |
| **MCP** client/server spans + W3C propagation | `forgesight-mcp` | `instrument_mcp_client(session)` |
| **FastAPI** request↔run correlation + flush-on-deploy | `forgesight-fastapi` | `app.add_middleware(AgentForgeMiddleware)` |
| **GitHub Actions** run↔commit/PR/job + cost summary | `forgesight-github` | `bootstrap()` |
| **LangGraph / CrewAI** auto-instrument (zero agent change) | `forgesight-adapters-*` | `LangGraphAdapter().instrument()` |
| **Budgets, policy & kill-switch** | `forgesight-governance` | `interceptors=["budget","policy","kill-switch"]` |
| **Eval scores & human feedback** | `forgesight-eval` | `record_evaluation("faithfulness", score=0.91)` |
| **Agent registry, ownership & chargeback** | `forgesight-registry` | `run_metadata_provider=reg.ownership_metadata` |

It tracks: agent runs · workflows · steps · LLM calls (tokens/cost/latency) · tool calls ·
MCP calls · metrics · traces · cost · lifecycle events + arbitrary business metadata.

---

## Quick start

```bash
pip install forgesight              # core + the batteries-included facade
pip install forgesight-otel         # one exporter (OTLP → any OTel backend)
```

```python
import forgesight
from forgesight import telemetry

# 1. Configure once at startup. Zero-config → console/in-memory in dev.
forgesight.configure(
    service_name="my-agent",
    exporters=["otlp"],
    exporter_config={"otlp": {"endpoint": "http://localhost:4317"}},
)

# 2. Wrap your work. Everything nests automatically (sync OR async).
async def run_agent(task: str):
    with telemetry.agent_run("my-agent", version="1.0.0", metadata={"team": "growth"}) as run:
        with run.step("plan"):
            ...
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            resp = await call_model(task)
            call.record_usage(input=resp.in_tok, output=resp.out_tok)   # cost derived for you
        with run.tool_call("search"):
            ...
```

**Prefer decorators?**

```python
from forgesight import instrument

@instrument(kind="agent", name="my-agent", version="1.0.0")
async def run_agent(task): ...

@instrument(kind="tool")          # a tool span named after the function
def search(q): ...
```

**On a framework? Don't touch the agent code** — install the adapter:

```python
from forgesight_adapters_langgraph import LangGraphAdapter
forgesight.configure()
LangGraphAdapter().instrument()                 # the unchanged graph is now instrumented
result = await my_compiled_graph.ainvoke({"task": "..."})
```

Configuration layers **file → env → kwargs** (last wins), so the same code reads
`forgesight.yaml` / `FORGESIGHT_*` env in production:

```yaml
# forgesight.yaml
service_name: my-agent
exporters: [otlp, langfuse]
exporter_config:
  otlp:     { endpoint: "${OTEL_COLLECTOR}" }
  langfuse: { public_key: "${LANGFUSE_PUBLIC_KEY}", secret_key: "${LANGFUSE_SECRET_KEY}" }
```

---

## Packages

A `uv` workspace with a three-tier model (ADR-0002): **contracts → runtime → integrations.**

- **`forgesight-api`** — locked contracts: the domain model + four `Protocol` SPIs. No I/O,
  no vendor SDKs. AgentForge and third parties depend on this to stay vendor-neutral.
- **`forgesight-core`** — the runtime: context propagation, span tree, async export
  pipeline, metrics, cost, events, interceptors, config, adapters, governance hooks.
- **`forgesight`** — the batteries-included facade most users install (`configure()`,
  `telemetry`, `@instrument`, entry-point auto-load).
- **Integrations** — `-otel`, `-langfuse`, `-datadog`, `-clickhouse`, `-prometheus`,
  `-mcp`, `-fastapi`, `-github`, `-adapters-langgraph`, `-adapters-crewai`,
  `-governance`, `-eval`, `-registry`. Each wraps exactly one backend/target; never on core.

See [`docs/`](./docs) for the requirements, architecture, ADRs, and the per-feature specs.

---

## Building with an AI coding assistant

ForgeSight is built to be edited by AI agents (Claude Code, Cursor, Copilot, …) without
drifting off-idiom. The conventions live in-repo and are loaded automatically:

- **[`AGENTS.md`](./AGENTS.md)** — the canonical, tool-agnostic rules (hard rules,
  anti-patterns, reading order, branch/PR loop). `CLAUDE.md` and any future tool file
  defer to it.
- **[`.claude/standards/`](./.claude/standards)** — coding, testing, git, docs, and
  configuration standards the assistant follows on every change.
- **[`.claude/checklists/`](./.claude/checklists)** — `pre-feature`, `pre-pr`, and
  `pre-release` gates to run before each milestone.
- **[`.claude/development-pipeline.md`](../../.claude/development-pipeline.md)** — the
  abstract per-feature workflow (branch → analyse → implement + tests ≥ 90% → ruff + mypy
  + pytest green → PR → CI green → squash-merge → next).
- **`.claude/state/`** — `current.md` (live snapshot) and `log.md` (milestone history) so
  an assistant can resume mid-stream.

The loop is enforced by the same gate CI runs: `ruff format` + `ruff check` +
`mypy --strict` + `pytest` (coverage ≥ 90% on Python 3.11–3.13). Install it locally with
`uv run pre-commit install`.

---

## Contributing

Contributions — issues, docs, fixes, new integration packages — are very welcome. Start
with **[CONTRIBUTING.md](./CONTRIBUTING.md)** and the
**[Code of Conduct](./CODE_OF_CONDUCT.md)**. Security reports go through GitHub private
advisories — see **[SECURITY.md](./SECURITY.md)**.

```bash
git clone https://github.com/Scaffoldic/forgesight.git && cd forgesight
uv sync --all-packages
uv run pytest
```

## License

[Apache License 2.0](./LICENSE) — see [NOTICE](./NOTICE).
