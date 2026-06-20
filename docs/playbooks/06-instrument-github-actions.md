# Playbook 06 — Instrument GitHub Actions

> Goal: correlate every agent run in CI with the commit/PR/job that triggered it, and drop a
> cost/usage summary into the workflow run — from one line.

## Install

```bash
pip install "forgesight[github,otel]"     # the integration + a backend
```

## One line in your job

```python
from forgesight_github import bootstrap

bootstrap()        # = configure() + attach GITHUB_* metadata + write a job summary on exit
```

`bootstrap()` detects the Actions environment (`in_github_actions()` → `GITHUB_ACTIONS == "true"`),
configures the SDK, attaches CI metadata to every run, and registers an exit hook that writes a
Markdown summary to `$GITHUB_STEP_SUMMARY`.

## What it attaches

Every run is stamped with `vcs.*` / `cicd.*` metadata derived from the standard `GITHUB_*`
env — repository, ref, SHA, actor, workflow, job, run id, and the PR number
(`vcs.change.id`, via `pr_number()`). The job summary lists run counts, tokens, and cost
(`DEFAULT_SUMMARY_METRICS`). For finer control, use `github_metadata()`,
`GitHubMetadataInterceptor`, or `SummaryCollector` directly.

## A complete workflow step

```yaml
jobs:
  agent:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write        # only if you want the OIDC token for attestation
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - name: Run agent with telemetry
        env:
          OTEL_COLLECTOR: ${{ secrets.OTEL_COLLECTOR }}
        run: uv run python run_agent.py     # bootstrap() inside picks up the env
```

```python
# run_agent.py
from forgesight_github import bootstrap
from forgesight import telemetry

bootstrap()
with telemetry.agent_run("ci-triage", version="1.0.0") as run:
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.record_usage(input=900, output=120)
# on exit: a cost/usage table appears in the job's Summary tab
```

## OIDC (optional)

`fetch_oidc_token()` returns the Actions OIDC token when `id-token: write` is granted — useful
for signed attestation of a run. The same OIDC mechanism publishes the packages to PyPI
(trusted publishing), tracked in `launch/`.

## Verify

Run the workflow; open the job → **Summary** tab for the cost table, and your backend for the
trace stamped with the commit/PR metadata.

> **Gotcha:** ambient `GITHUB_*` env leaks into *local* runs and tests too. If you call this
> outside CI, pass an explicit `env=` or scrub the ambient vars so you don't misattribute runs.

Full reference: [GitHub Actions runbook](../runbooks/github-actions.md).

## Next

→ [07 — Governance & budgets](./07-governance-and-budgets.md)
