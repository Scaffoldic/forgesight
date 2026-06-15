# forgesight-governance

Cost budgets, policy enforcement, and a kill-switch for [ForgeSight](https://github.com/Scaffoldic/forgesight).
The SDK already knows what every LLM call costs and already has a veto point (the interceptor
chain) — this package turns that cost signal into a **control**: stop the spend, deny a model,
or cut off one scope's runs in seconds.

```bash
pip install forgesight-governance
```

```python
import forgesight
from forgesight_governance import BudgetInterceptor, PolicyInterceptor, KillSwitch

forgesight.configure(interceptors=[
    KillSwitch.from_config(),      # cheapest veto first
    PolicyInterceptor.from_config(),
    BudgetInterceptor.from_config(),
])
```

Or purely by name (entry-point auto-load) — `interceptors: [{name: kill-switch}, {name: policy}, {name: budget}]` plus a `governance:` block.

## What you get

- **Budgets.** Per-run / per-team / per-repo / per-environment USD or token caps, keyed on the
  business metadata the SDK already attaches (FR-5). A breach raises `BudgetExceeded` →
  `RunStatus.BUDGET_EXCEEDED`; the run record still flushes (telemetry is never lost).
- **Policy.** First-match-wins rules over metadata: `deny` a model set (e.g. unpriced/preview
  models in prod), `allow`-list the models a team may call, or `redact` content for PII-tagged
  runs. A denial raises `PolicyDenied` → `RunStatus.GUARDRAIL`.
- **Kill-switch.** Flip `FORGESIGHT_KILL_REPO_PAYMENTS_AGENT=true` (or a file entry) and every
  run for that repo trips immediately, while every other agent keeps running — no redeploy.

```yaml
governance:
  budgets:
    per_run: { usd: 5.0 }
    per_team: { growth: { usd: 200.0 }, research: { usd: 2000.0 } }
    on_breach: "raise"          # raise | drop | mark
  policies:
    rules:
      - match: { environment: "prod" }
        action: "deny"
        models: ["*-experimental", "gpt-*-preview"]
      - match: { pii: "true" }
        action: "redact"
  kill_switch:
    source: "env"               # env | file
```

A budget/policy/kill trip is the **one sanctioned case** where an interceptor's exception
propagates to the caller (a deliberate control, not a telemetry failure, P6). All of
`governance.*` is absent → disabled; install is necessary, config is the enabler.

## Out of scope (0.3)

Fleet-wide / cross-process caps (process-local for now; the `BudgetCap` + total-store split
leaves room for a shared store), spend forecasting, and a general policy language.

## License

Apache-2.0
