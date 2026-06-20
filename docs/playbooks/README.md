# Playbooks — setup & how-to guides

Task-oriented guides that take you from zero to instrumented. Start at the top;
each one stands alone.

| # | Playbook | You'll end up with |
|---|---|---|
| 01 | [Install ForgeSight](./01-install.md) | the SDK + the right extras in your project |
| 02 | [Instrument your agent](./02-instrument-your-agent.md) | runs, LLM/tool/step spans, tokens & cost |
| 03 | [Run locally with Docker](./03-run-locally-with-docker.md) | traces/metrics in Jaeger, Prometheus, ClickHouse |
| 04 | [Ship to a backend](./04-ship-to-a-backend.md) | telemetry flowing to your platform of choice |
| 05 | [Instrument a FastAPI service](./05-instrument-a-fastapi-service.md) | request↔run correlation + flush-on-deploy |
| 06 | [Instrument GitHub Actions](./06-instrument-github-actions.md) | CI runs correlated to commit/PR + a cost summary |
| 07 | [Governance & budgets](./07-governance-and-budgets.md) | cost caps, policy, and a kill-switch |

**Looking for reference, not a walkthrough?** The [runbooks](../runbooks/) document each
backend/integration in depth — config knobs, what it emits, and troubleshooting.

The mental model in one line: **installing a package *enables* a backend; one line of
config *selects* it. Your agent code never changes.**
