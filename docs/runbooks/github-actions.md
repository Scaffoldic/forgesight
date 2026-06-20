# GitHub Actions integration runbook

> One line in CI to attach `GITHUB_*` metadata to every record and write a per-job run summary on exit. **Extra:** `pip install "forgesight[github]"` · **Spec:** [feat-018](../features/feat-018-github-actions-integration.md)

## What it does

`bootstrap()` does three things, in order: reads the runner's `GITHUB_*` environment into business
metadata, `configure()`s the SDK and attaches that metadata to *every* record via an interceptor,
and registers an exit hook that flushes telemetry (so an ephemeral runner never drops it) and
appends a markdown run summary to `$GITHUB_STEP_SUMMARY`. Outside CI it falls back to a plain
`configure()` and warns once, so the same script runs locally unchanged.

## When to use it

- A CI agent (PR reviewer, release bot) runs inside GitHub Actions and you want every span tagged
  with repo / sha / ref / run / PR / workflow / job for cost attribution and correlation.
- You want a "run: ok · cost $0.12 · 38s · 3 tool calls" block in the Actions job summary with no
  author effort.
- You need a guaranteed flush before the ephemeral runner tears down.

## Install

```bash
pip install "forgesight[github]"     # facade extra
# or the standalone package:
pip install forgesight-github        # depends on forgesight-core
```

## Set up

One line at the top of your CI script:

```python
from forgesight_github import bootstrap

bootstrap()   # configure() + attach GITHUB_* metadata + write job summary on exit
```

In a workflow, no extra wiring is needed — `GITHUB_*` is already in the runner env:

```yaml
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python review_agent.py    # bootstrap() reads GITHUB_* automatically
```

Options:

```python
bootstrap(
    write_summary=True,                    # append the run summary to $GITHUB_STEP_SUMMARY
    summary_metrics=("status", "cost_usd", "duration_ms", "n_tool_calls"),  # DEFAULT_SUMMARY_METRICS
    oidc=False,                            # fetch a runner OIDC token, handed off via FORGESIGHT_OTLP_TOKEN
    extra_metadata={"team": "platform"},   # extra business-metadata keys
)
```

Lower-level entry points if you need them: `github_metadata()` returns the `GITHUB_*` → attribute
mapping; `pr_number(env)` parses the PR number from the event payload JSON;
`GitHubMetadataInterceptor(metadata)` is the interceptor that merges that metadata onto each
record; `SummaryCollector` is the `EventListener` that tallies runs/cost/tool-calls; and
`write_summary(collector, fields)` renders the markdown block.

The `forgesight.integrations` entry point (group `forgesight.integrations`, name **`github`** →
`forgesight_github:install`) stashes config defaults (e.g. `write_summary`) for `bootstrap()` to
read.

## What it emits / correlates

`github_metadata()` maps the runner env onto `vcs.*` / `cicd.*` semconv keys (`GITHUB_ENV_MAP`),
plus the parsed PR number. Absent fields are omitted, never fabricated:

| Env var | Attribute key |
| --- | --- |
| `GITHUB_REPOSITORY` | `vcs.repository.name` |
| `GITHUB_SHA` | `vcs.ref.head.revision` |
| `GITHUB_REF` | `vcs.ref.head.name` |
| `GITHUB_RUN_ID` | `cicd.pipeline.run.id` |
| `GITHUB_RUN_ATTEMPT` | `cicd.pipeline.run.attempt` |
| `GITHUB_WORKFLOW` | `cicd.pipeline.name` |
| `GITHUB_JOB` | `cicd.pipeline.task.name` |
| `GITHUB_ACTOR` | `vcs.change.author` (agentforge extension) |
| `GITHUB_EVENT_NAME` | `cicd.pipeline.run.trigger` |
| PR number (from event payload) | `vcs.change.id` (via `PR_NUMBER_KEY`) |

The `GitHubMetadataInterceptor` merges these onto every record's attributes with `setdefault`, so
per-call metadata the author set explicitly always wins. This makes "spend on PR #1234" a one-line
backend query.

**Job summary:** the `SummaryCollector` tallies run status, summed LLM `cost_usd`, total
`duration_ms`, and tool-call count (`TOOL_EXECUTED` + `MCP_EXECUTED`). On exit a markdown block —
`### 🤖 ForgeSight agent run(s)` with the `summary_metrics` fields — is appended to the file named
by `$GITHUB_STEP_SUMMARY`, which the Actions UI renders on the job page.

## Operate it

Runtime requirements: a configured exporter (`bootstrap()` calls `configure()`); CI detection is
`in_github_actions()`, which is true only when `GITHUB_ACTIONS == "true"`. The summary write is
best-effort and never fails the job.

Behaviour inside a workflow:

- `bootstrap()` detects CI via `in_github_actions()`. In CI it reads `GITHUB_*`, attaches the
  interceptor, registers the `atexit` flush + summary hook.
- The summary lands in the Actions UI under the job (from `$GITHUB_STEP_SUMMARY`). Run it on a
  `pull_request` event to get `vcs.change.id` populated from the event payload.
- Not in CI: `bootstrap()` does a plain `configure()` and warns once — no metadata, no summary.

Verify: in a workflow run, open the job page and confirm the `🤖 ForgeSight agent run` summary
block appears; in your backend, filter spans by `vcs.repository.name` / `cicd.pipeline.run.id` /
`vcs.change.id` and confirm they are populated.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| No `GITHUB_*` metadata on spans | `bootstrap()` ran outside CI (`GITHUB_ACTIONS` not `"true"`) | Run inside Actions, or pass `extra_metadata` for local context |
| No job summary in the UI | `$GITHUB_STEP_SUMMARY` unset, or `write_summary` disabled | Run in Actions (it sets the file); ensure `write_summary=True` / `FORGESIGHT_GITHUB_SUMMARY` |
| `vcs.change.id` (PR number) missing | Not a `pull_request*` event, or unreadable `GITHUB_EVENT_PATH` payload | Expected on non-PR events; the parser never fabricates a value |
| OIDC token not obtained | No runner id-token endpoint (or `oidc=False`) | Grant `id-token: write` permissions; falls back to configured exporter auth, logs a warning |
| Tests/local runs pick up real repo metadata | **Ambient `GITHUB_*` env leaks into local/test runs** | Pass an explicit `env=` mapping to `github_metadata()` / `pr_number()`, or clear `GITHUB_*` (and `GITHUB_ACTIONS`) in the test environment |
| Summary write error | Unwritable summary path | Non-fatal by design — it logs a warning and never fails the job; export is non-blocking and `export()` returns failure, never raises |

## Reference

- Feature spec: [feat-018 GitHub Actions integration](../features/feat-018-github-actions-integration.md)
- Package: [`packages/forgesight-github`](../../packages/forgesight-github)
- Playbook: [Install ForgeSight](../playbooks/01-install.md)
- Playbook: [Instrument your agent](../playbooks/02-instrument-your-agent.md)
- Playbook: [Instrument GitHub Actions](../playbooks/06-instrument-github-actions.md)
