# Playbook 07 — Governance & budgets

> Goal: put cost caps, policy rules, and an emergency kill-switch in front of your agents —
> as interceptors, with no agent-code change.

Governance runs on the **interceptor chain**: small policies that observe records as they flow
and can **halt the run** by raising a `GovernanceSignal`. This is the one deliberate exception
to "telemetry never breaks a run" — it's a control plane, and stopping is the point.

## Install

```bash
pip install "forgesight[governance]"
```

## Enable the three controls

Activate the interceptors with `interceptors: [...]`, and configure them in a top-level
`governance:` block. Each interceptor name resolves to its registered `from_config`, which
reads its slice of `governance:`. Order is intentional — kill-switch (cheapest veto), then
policy, then budget — so a killed or denied run never even projects cost.

```yaml
# forgesight.yaml
interceptors: [kill-switch, policy, budget]

governance:
  budgets:
    per_run:
      usd: 0.50                 # raise if a run would exceed $0.50
      tokens: 2_000_000         # …or 2M tokens, whichever trips first
    per_team:
      growth: { usd: 200.0 }    # caps keyed on the `team` run metadata
    on_breach: "raise"          # "raise" | "drop" | "mark"  (default: raise)

  policies:
    rules:
      - match: { environment: "prod" }
        action: "deny"
        models: ["*-experimental", "gpt-*-preview"]   # no preview models in prod
        reason: "prod may only call GA models"

  kill_switch:
    source: "env"               # "env" | "file"
    # env reads FORGESIGHT_KILL_<SCOPE>_<KEY>=true per call;
    # file: set source: "file", file_path: /etc/forgesight/kill, poll_seconds: 5
```

Load it (auto-discovered as `forgesight.yaml`, or point at it):

```python
import forgesight
forgesight.configure(config_file="forgesight.yaml")
```

**Prefer code?** Pass interceptor instances directly — same effect, no YAML:

```python
import forgesight
from forgesight_governance import BudgetInterceptor, BudgetCap, BudgetScope

forgesight.configure(interceptors=[
    BudgetInterceptor(
        caps=[
            BudgetCap(scope=BudgetScope.RUN, usd=0.50, tokens=2_000_000),
            BudgetCap(scope=BudgetScope.TEAM, key="growth", usd=200.0),
        ],
        on_breach="raise",
    ),
])
```

## What each one does

| Control | Class | Fires when | Effect |
|---|---|---|---|
| **Budget** | `BudgetInterceptor` / `BudgetCap` | accumulated cost on completed LLM records exceeds the cap | raises `BudgetExceeded` (a `GovernanceSignal`) → run fails |
| **Policy** | `PolicyInterceptor` / `PolicyRule` / `PolicyAction` | a record matches a deny rule | raises `PolicyDenied` → run halts |
| **Kill-switch** | `KillSwitch` + `EnvKillSwitchSource` / `FileKillSwitchSource` | the env var / file flag is set | raises `KillSwitchEngaged` → all runs halt |

Budgets accumulate **process-local** cost from completed LLM records (`record.llm.cost_usd`);
budget/policy/kill-switch all short-circuit harmlessly when a record carries no LLM data.
A tripped control raises a `GovernanceSignal`, which the run scope maps to a **failed** run so
your caller sees the stop.

## Verify it trips

```python
import forgesight
from forgesight import telemetry
from forgesight_governance import BudgetInterceptor, BudgetCap, BudgetScope, BudgetExceeded

forgesight.configure(interceptors=[
    BudgetInterceptor(caps=[BudgetCap(scope=BudgetScope.RUN, usd=0.0001)], on_breach="raise"),
])

try:
    with telemetry.agent_run("spendy") as run:
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            call.record_usage(input=100000, output=100000)   # blows the tiny cap
except BudgetExceeded as e:
    print("halted as expected:", e)
```

Toggle the kill-switch without redeploying:

```bash
export FORGESIGHT_KILL_RUN_spendy=true     # env source: FORGESIGHT_KILL_<SCOPE>_<KEY>=true
export FORGESIGHT_KILL_TEAM_growth=true    # halt just the "growth" team
# or (source: file) add a `team:growth` line to the file your FileKillSwitchSource polls
```

## Operational notes

- Budgets are per-process today; a cross-process/shared budget store is a tracked follow-up.
- Pre-call cost *projection* (cap before the spend) is on the roadmap; today the cap is
  enforced on completed LLM records.
- Governance signals are intentional run-stoppers — distinct from exporter failures, which are
  always swallowed and counted.

Full reference: [governance runbook](../runbooks/governance.md).

## Done

You've covered install → instrument → run locally → ship → web/CI → governance. Browse the
[runbooks](../runbooks/) for per-backend depth.
