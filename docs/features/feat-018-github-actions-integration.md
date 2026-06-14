# feat-018: GitHub Actions integration

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-018 |
| **Title** | GitHub Actions integration — one-line CI bootstrap; run↔commit/PR/job correlation |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-github` |
| **Depends on** | feat-002, feat-010 |
| **Blocks** | none |

---

## 1. Why this feature

Agentic CI is now common: a GitHub Actions workflow runs an agent to triage an
issue, review a PR, draft a changelog, or fix a failing test. These runs cost
money and sometimes misbehave, and they are the *hardest* to debug because they
are ephemeral — the runner is gone seconds after the job ends.

Concrete pains today:

- An agent in CI burned $40 over a weekend across a flurry of PRs. Which PRs?
  Which commits? Which workflow? The telemetry exists (the SDK recorded every
  run's cost) but nothing ties a run to the commit / PR / job that triggered
  it, so cost attribution is manual archaeology through Actions logs.
- A reviewer agent left a bad comment on a PR. To debug, the engineer needs the
  run's span tree — but the run had no link to the PR number or the SHA, so
  finding *that* run among thousands is guesswork.
- The job's summary page shows nothing useful — just raw logs. The team wants
  "this agent run cost $0.12, took 38s, status ok" on the job summary, but each
  team hand-rolls writing to `$GITHUB_STEP_SUMMARY`.

The data the SDK needs to fix all of this is already in the runner's
environment (`GITHUB_*`); it just needs to be read and attached.

## 2. Why this belongs in the SDK (vs each team wiring it by hand)

- **The `GITHUB_*` → business-metadata mapping is boilerplate every CI agent
  re-writes.** Reading `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_REF`,
  `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_WORKFLOW`, `GITHUB_JOB`,
  `GITHUB_ACTOR`, `GITHUB_EVENT_NAME`, and the PR number (parsed from the event
  payload, not a plain env var) — and attaching them as run-scoped metadata
  (FR-5) — is identical for every CI agent. Shipping it once means every CI
  agent run is correlated to commit/PR/job/workflow the same way, fleet-wide.
- **Cost attribution across CI is a platform property, not a per-repo one.**
  FinOps wants "agent CI spend by repo / by PR / by workflow" across the org.
  That only works if every CI run tags the *same* metadata keys with the *same*
  meaning. A per-team convention guarantees the chargeback query is impossible.
- **The step-summary write is the kind of thing teams skip under deadline.** A
  run summary (cost / duration / status) on the job page is high-value and
  low-glamour; left to teams it never gets written. Owning it means it appears
  by installation.
- **OIDC exporter auth is security-sensitive and easy to do wrong.** CI should
  authenticate to the collector via the runner's OIDC token, not a long-lived
  secret baked into the workflow. Centralising the OIDC-friendly exporter wiring
  keeps that pattern correct and consistent.
- **Anti-pattern if left to teams:** every CI agent parses `GITHUB_*`
  differently (or forgets the PR number entirely), cost can't be rolled up, the
  job summary is empty, and someone eventually commits a static collector token.

Framework-agnostic (P3): it correlates *any* agent run that happens to be in CI,
ships as its own package wrapping one target (the GitHub Actions environment,
P1/P2), and is never added to core.

## 3. How consuming agents/teams benefit

- **Before:** a CI agent author writes ~25–40 lines that read a dozen `GITHUB_*`
  vars, parse the event JSON for the PR number, stuff them into metadata, and
  (maybe) append a summary line to `$GITHUB_STEP_SUMMARY`. **After:** one line —
  `from forgesight_github import bootstrap; bootstrap()` — and every run in
  that job is tagged with repo / sha / ref / run_id / run_attempt / workflow /
  job / actor / event_name / PR number, with a cost+duration+status summary
  written to the job page on exit.
- **Cost attribution for free (FR-5).** Every CI run carries
  `vcs.repository.name`, `vcs.ref.head.revision` (sha), PR number, workflow, and
  job as run-scoped metadata, so "agent spend on PR #1234" or "spend by
  workflow" is a one-line query in the backend — no log spelunking.
- **Debug the exact run.** A bad PR comment ⇒ filter telemetry by PR number +
  SHA ⇒ the run's span tree, instantly.
- **A useful job summary, automatically.** The Actions UI shows
  "run: ok · cost $0.12 · 38s · 3 tool calls" without the author touching
  `$GITHUB_STEP_SUMMARY`.
- **Safe exporter auth.** Opt into OIDC and the collector is reached with the
  runner's short-lived token — no static secret in the workflow.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — the entire CI wiring (one line)
from forgesight_github import bootstrap
bootstrap()                          # configure() + attach GITHUB_* + summary-on-exit

# Unchanged agent code — every run in this job is now correlated to the
# commit / PR / job / workflow, and a summary lands on the job page at exit.
result = await pr_reviewer.run(task)
```

```yaml
# .github/workflows/review.yml  (no SDK-specific YAML needed beyond install)
jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      id-token: write        # only if using OIDC exporter auth
    steps:
      - uses: actions/checkout@v4
      - run: pip install forgesight-github
      - run: python review_agent.py     # bootstrap() reads GITHUB_* automatically
        env:
          FORGESIGHT_EXPORTER: otlp
          FORGESIGHT_OTLP_ENDPOINT: ${{ vars.OTEL_COLLECTOR }}
```

```typescript
// typescript (parity sketch)
import { bootstrap } from '@agentforge/sdk-github';
bootstrap();   // reads GITHUB_* env, configure(), writes job summary on exit
```

### 4.2 Public API / contract

```python
# forgesight_github/__init__.py

def bootstrap(
    *,
    write_summary: bool = True,           # append run summary to $GITHUB_STEP_SUMMARY
    summary_metrics: "Sequence[str]" = ("status", "cost_usd", "duration_ms", "n_tool_calls"),
    oidc: bool = False,                   # OIDC-friendly exporter auth (id-token)
    extra_metadata: "Mapping[str, str] | None" = None,
) -> None:
    """One-line CI bootstrap: forgesight.configure(), attach GITHUB_* as
    run-scoped business metadata (FR-5), and (default) write a per-run summary
    to $GITHUB_STEP_SUMMARY on process exit. No-op (warns once) when not in CI.
    """

def github_metadata() -> "dict[str, str]":
    """Return the GITHUB_* → metadata mapping (repo, sha, ref, run_id,
    run_attempt, workflow, job, actor, event_name, pr_number). Pure; for
    callers wiring metadata manually.
    """
```

```typescript
// @agentforge/sdk-github
export interface BootstrapOptions {
  writeSummary?: boolean;
  summaryMetrics?: string[];
  oidc?: boolean;
  extraMetadata?: Record<string, string>;
}
export function bootstrap(opts?: BootstrapOptions): void;
export function githubMetadata(): Record<string, string>;
```

Stability: `bootstrap()` + `github_metadata()` are the public surface, **stable**
for 0.2. The exact metadata key names track the OTel VCS/CICD conventions and
may gain keys (additive) as those stabilise.

### 4.3 Internal mechanics

`bootstrap()` does three things, in order:

```
1. metadata = github_metadata()        # read GITHUB_* + parse event payload
      GITHUB_REPOSITORY  → vcs.repository.name              (FR-5)
      GITHUB_SHA         → vcs.ref.head.revision
      GITHUB_REF         → vcs.ref.head.name
      GITHUB_RUN_ID      → cicd.pipeline.run.id
      GITHUB_RUN_ATTEMPT → cicd.pipeline.run.attempt
      GITHUB_WORKFLOW    → cicd.pipeline.name
      GITHUB_JOB         → cicd.pipeline.task.name
      GITHUB_ACTOR       → vcs.change.author  (agentforge.* extension where no semconv exists)
      GITHUB_EVENT_NAME  → cicd.pipeline.run.trigger
      PR number          → vcs.change.id   (parsed from $GITHUB_EVENT_PATH JSON
                                            for pull_request* events; not a plain env var)

2. forgesight.configure(default_metadata=metadata)   # feat-010: run-scoped
      every agent_run / workflow_run inherits these as span attributes (FR-5)
      OIDC: when oidc=True, exchange the runner id-token for collector creds and
            pass to the exporter (no static secret)

3. atexit / SIGTERM hook:
      force_flush() + shutdown()                    # feat-003: don't lose CI telemetry
      if write_summary: append a markdown block to $GITHUB_STEP_SUMMARY
            "### agent run\n status · cost · duration · n_tool_calls"
```

**PR number** is the only non-trivial field: it is not in a plain env var. For
`pull_request` / `pull_request_target` events it is read from the event payload
JSON at `$GITHUB_EVENT_PATH` (`.pull_request.number` / `.number`); for other
events it is absent (not fabricated — spec rule: don't fabricate ids).

**Default metadata** rides via the feat-010 config mechanism so it is attached
at *run scope* (FR-5): set once, inherited by every child span, exactly the
correlation semantics requirements §FR-5 demands.

**Summary** is written on exit (not per run) by aggregating the runs the SDK saw
in the process — the common CI case is one run per job, so the summary reflects
it directly; multi-run jobs get a rollup. The write is best-effort and never
fails the job (P6).

**Not in CI** — when `GITHUB_ACTIONS` is unset, `bootstrap()` falls back to a
plain `configure()` and warns once, so the same script runs locally.

### 4.4 Module packaging

`forgesight-github` is its own integration package wrapping exactly one
target (the GitHub Actions environment) and is **never** added to core
(P1/P3). It depends on `forgesight-core` only — reading env + the event JSON
needs no GitHub SDK; OIDC uses stdlib HTTP against the runner's token endpoint.

```bash
pip install forgesight-github
```

```yaml
# forgesight.yaml (optional; bootstrap() defaults work with none of this)
integrations:
  github:
    enabled: true
    write_summary: true
    oidc: false
```

Entry-point: `forgesight.integrations` →
`github = forgesight_github:install`. `bootstrap()` is the explicit
one-liner; the entry point lets `configure()` pick up GitHub metadata even when
a host calls `configure()` directly with auto-load on.

### 4.5 Configuration

| Key | Env | Default | Meaning |
|---|---|---|---|
| `integrations.github.write_summary` | `FORGESIGHT_GITHUB_SUMMARY` | `true` | Append the run summary to `$GITHUB_STEP_SUMMARY` on exit. |
| `integrations.github.summary_metrics` | — | `status,cost_usd,duration_ms,n_tool_calls` | Which fields the summary shows. |
| `integrations.github.capture_env` | `FORGESIGHT_GITHUB_ENV` | the documented `GITHUB_*` set | Which env keys to attach. Restrict to drop e.g. `actor` for privacy. |
| `integrations.github.oidc` | `FORGESIGHT_GITHUB_OIDC` | `false` | Exchange the runner OIDC id-token for collector credentials (requires `id-token: write`). |

Validation: when `oidc: true` but no id-token endpoint is present, warn and fall
back to configured exporter auth (never fail the job, P6); unknown keys in
`capture_env` are ignored (forward-compat with new `GITHUB_*` vars).

## 5. Plug-and-play & upgrade story

Add later: `pip install forgesight-github` + one `bootstrap()` line in the CI
entry script — no agent-code change (P2). Remove by deleting the line +
uninstalling. Minor upgrades may map newly-stabilised `cicd.*` / `vcs.*`
attributes (additive) and add summary fields behind defaults; `bootstrap()` /
`github_metadata()` signatures stay (P5).

## 6. Cross-language parity

Identical: the `GITHUB_*` → metadata key mapping, PR-number parsing from the
event payload, run-scoped attachment (FR-5), the step-summary format,
flush-on-exit, OIDC opt-in, not-in-CI fallback. Differs: Python reads env +
`json` + `atexit`; TS reads `process.env` + `fs` + an exit hook. No field
deferred in either language for 0.2.

## 7. Test strategy

- **Unit:** `github_metadata()` maps every documented `GITHUB_*` var to the
  right key; PR number parsed from a fixture `event.json` for `pull_request`,
  absent for `push`; `capture_env` restriction honoured.
- **Bootstrap:** `configure()` called with run-scoped metadata; metadata appears
  on a child run's span (in-memory exporter).
- **Summary:** with a temp `$GITHUB_STEP_SUMMARY`, the markdown block is
  appended with the configured fields; summary write failure does not fail the
  process (P6).
- **Not in CI:** `GITHUB_ACTIONS` unset ⇒ plain `configure()` + one warning.
- **OIDC:** when enabled, the id-token exchange is attempted; absent endpoint ⇒
  graceful fallback, job continues.
- **Flush-on-exit:** exit hook drains the queue to the in-memory exporter.
- **Example:** a sample workflow + agent producing a correlated run end-to-end.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| PR number absent for non-PR events | Read from the event payload only for `pull_request*`; absent (not fabricated) otherwise. |
| `GITHUB_*` semconv keys still stabilising (`cicd.*` / `vcs.*` are Development) | Map to current conventions; emit `agentforge.*` extension where no semconv exists; re-pin additively (P5). |
| OIDC token exchange varies by collector | OIDC wiring isolated behind the `oidc` flag; fall back to configured auth; document collector-specific setup. |
| Summary spam on multi-run jobs | One rollup summary on exit, not one per run. |
| Leaking actor / internal repo names | `capture_env` lets teams drop sensitive keys; content (not env) still gated by P7. |

## 9. Out of scope

- **A GitHub Action (`action.yml`) wrapper** — this is a Python/TS package you
  call from a `run:` step, not a marketplace action. A thin action wrapper is a
  possible follow-up.
- **Posting telemetry back as PR comments / checks** — emit to a backend; the
  GitHub UI for results is a separate concern (and requirements §11: no
  dashboard).
- **CI providers beyond GitHub Actions** (GitLab CI, Buildkite, CircleCI) — each
  is its own future integration package; this one targets GitHub Actions.
- **Storing or hosting CI telemetry** — the SDK is a client (requirements §11).

## 10. References

- [`../requirements.md`](../requirements.md) — FR-5 (business metadata; CI correlation), §5 personas (CI / automation, FinOps)
- [`../design/architecture.md`](../design/architecture.md) §5 (packaging), §7 (lifecycle / flush)
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.6 (force_flush / shutdown on exit)
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P2, P3, P6, P7
- feat-002 (runtime / instrumentation API), feat-010 (configure / default metadata)
- OpenTelemetry VCS & CICD semconv: <https://github.com/open-telemetry/semantic-conventions>
