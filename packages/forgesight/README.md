# ForgeSight

**Instrument any AI agent in a few lines — then ship traces, cost, metrics, evals, budgets,
and a tamper-evident audit trail to any backend by changing one line of config.
OpenTelemetry-first. Vendor-neutral. Never an agent-code change.**

[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](https://github.com/Scaffoldic/forgesight/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11_|_3.12_|_3.13-blue.svg)](https://pypi.org/project/forgesight/)

▶️ **[See the 30-second demo](https://github.com/Scaffoldic/forgesight#forgesight)** — instrument
an agent and watch the trace, cost, and a verified tamper-evident audit trail appear.

`forgesight` is the batteries-included facade — the package most people install.

```bash
pip install "forgesight[otel]"
```

```python
import forgesight
from forgesight import telemetry

forgesight.configure(exporters=["otel"])          # pick a backend by name — that's it

with telemetry.agent_run("pr-reviewer", version="2.1.0", metadata={"team": "platform"}) as run:
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        resp = await client.messages.create(...)
        call.record_usage(input=resp.usage.input_tokens, output=resp.usage.output_tokens)
    with run.tool_call("github_get_diff"):
        diff = gh.get_diff(pr)
```

Cost, token usage, trace nesting, metrics, and multi-backend fan-out come for free. Swap
`otel` → `langfuse`, `datadog`, `clickhouse`, `prometheus` — the agent code never moves.

## What you get

- **🔌 Vendor-neutral.** The core depends on *no* backend or model-provider SDK. Backends are
  packages you install and select by config.
- **📐 OpenTelemetry-first.** GenAI semantic conventions, so any OTLP backend works with no
  dedicated package.
- **💰 Cost built in.** Token usage → USD via a pluggable pricing table — the same number
  everywhere — plus live attribution by team and pre-call budget projection.
- **🚦 Non-blocking & fault-isolated.** Export runs on a background worker; a backend outage
  never breaks a run.
- **🔒 Secure by default.** Content capture is opt-in; redaction runs before export.
- **🧾 Accountability.** A tamper-evident, hash-chained **audit trail** with a compliance
  query/export surface.

## Install backends & integrations as extras

```bash
pip install "forgesight[otel]"                      # one backend (OTLP → any OTel platform)
pip install "forgesight[otel,langfuse,datadog]"     # several
pip install "forgesight[all]"                       # everything except the heavy CrewAI tree
```

Extras: `otel` · `prometheus` · `langfuse` · `clickhouse` · `datadog` · `mcp` · `fastapi` ·
`github` · `governance` · `eval` · `registry` · `audit` · `adapters-langgraph` ·
`adapters-crewai`. Installing a package *enables* a backend; config *selects* it.

## Docs

- **[README & guides](https://github.com/Scaffoldic/forgesight)** — full docs, playbooks, runbooks
- **[Quick start](https://github.com/Scaffoldic/forgesight/blob/main/docs/playbooks/01-install.md)** ·
  **[Run locally with Docker](https://github.com/Scaffoldic/forgesight/blob/main/docs/playbooks/03-run-locally-with-docker.md)** ·
  **[Examples](https://github.com/Scaffoldic/forgesight/tree/main/examples)**

## License

[Apache-2.0](https://github.com/Scaffoldic/forgesight/blob/main/LICENSE)
