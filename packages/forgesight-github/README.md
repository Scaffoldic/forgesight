# forgesight-github

The GitHub Actions integration for [ForgeSight](https://github.com/Scaffoldic/forgesight).
One line in your CI entry script correlates every agent run with the commit / PR / job /
workflow that triggered it, and writes a cost+duration+status summary to the job page.

```bash
pip install forgesight-github
```

```python
from forgesight_github import bootstrap
bootstrap()   # configure() + attach GITHUB_* metadata + job summary on exit

# Unchanged agent code — every run in this job is now correlated and flushed cleanly.
result = await pr_reviewer.run(task)
```

```yaml
# .github/workflows/review.yml
jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      id-token: write          # only if using OIDC exporter auth
    steps:
      - uses: actions/checkout@v4
      - run: pip install forgesight-github
      - run: python review_agent.py
        env:
          FORGESIGHT_EXPORTERS: otlp
          FORGESIGHT_OTLP_ENDPOINT: ${{ vars.OTEL_COLLECTOR }}
```

## What you get

- **Run↔commit/PR/job link for free.** Every run carries `vcs.repository.name`,
  `vcs.ref.head.revision` (sha), `vcs.ref.head.name` (ref), `vcs.change.id` (PR number),
  `cicd.pipeline.run.id` / `.attempt`, `cicd.pipeline.name` (workflow), and
  `cicd.pipeline.task.name` (job) as run-scoped metadata (FR-5), so "agent spend on PR #1234"
  or "spend by workflow" is a one-line backend query.
- **PR number resolved correctly.** Parsed from the event payload JSON (`$GITHUB_EVENT_PATH`)
  for `pull_request*` events — absent (not fabricated) otherwise.
- **A useful job summary, automatically.** A markdown block —
  `status · cost_usd · duration_ms · n_tool_calls` — is appended to `$GITHUB_STEP_SUMMARY`
  on exit. Best-effort; never fails the job.
- **Zero lost CI telemetry.** An `atexit` hook calls `force_flush()` + `shutdown()` so the
  ephemeral runner never drops the buffered batch.
- **Safe exporter auth (opt-in).** `bootstrap(oidc=True)` fetches the runner's short-lived
  OIDC id-token (requires `id-token: write`) instead of a static secret; falls back to
  configured auth if unavailable.
- **Runs locally too.** Outside CI (`GITHUB_ACTIONS` unset) it falls back to a plain
  `configure()` and warns once.

## Configuration

| Key | Env | Default |
|---|---|---|
| `write_summary` | `FORGESIGHT_GITHUB_SUMMARY` | `true` |
| `oidc` | — (kwarg) | `false` |
| `capture_env` | — (kwarg) | the documented `GITHUB_*` set |

## License

Apache-2.0
