# Governance runbook

> Turn ForgeSight's cost signal into a control: budget caps, declarative policy, and a kill-switch that halt a run *before* the spend happens. **Extra:** `pip install "forgesight[governance]"` · **Spec:** [feat-020](../features/feat-020-cost-budgets-and-governance.md)

## What it does

The governance package ships three interceptors that ride the locked `Interceptor` SPI and act only on LLM-call records: `BudgetInterceptor` accumulates per-scope spend and enforces caps, `PolicyInterceptor` applies declarative allow/deny/redact rules over business metadata, and `KillSwitch` vetoes a call when its scope key is tripped in a hot-reloadable source. All three are keyed on the business metadata (`team` / `repo` / `environment`) the SDK already attaches at run scope. When a control fires it raises a `GovernanceSignal`, which halts the agent's work while the run record still flushes — telemetry is never lost, only the agent stops.

## When to use it

- You need a hard per-run / per-team / per-repo / per-environment USD or token cap, enforced in-flight rather than discovered on the invoice.
- You need to deny un-priced or preview models in `environment=prod`, force content capture off for PII-tagged runs, or allow-list the models a team may call.
- You need an incident kill-switch that cuts one scope's LLM spend in seconds, with no redeploy and no key rotation, while every other agent keeps running.
- You want this uniform across the fleet so FinOps can reason about it — not re-implemented per agent.

## Install

```bash
pip install "forgesight[governance]"
```

This pulls `forgesight-governance`, which depends only on `-api` and `-core` — **no vendor SDK** (P1). Installing it does nothing until you configure a cap, rule, or kill-switch (`governance.*` absent → disabled). The three interceptors register under the existing `forgesight.interceptors` entry-point group — `budget`, `policy`, and `kill-switch` — each pointing at its class's `from_config` (e.g. `budget = forgesight_governance.budget:BudgetInterceptor.from_config`).

## Set up / Configure

### Enable by name (the preferred, pure-config path)

```yaml
# forgesight.yaml
interceptors:
  - kill-switch     # cheapest veto first
  - policy
  - budget          # project cost last — a killed/denied run never even projects
```

Order matters and is config-controlled: kill-switch, then policy, then budget. Each name resolves to the registered `from_config`, which reads its block of the `governance:` config.

### Programmatic equivalent

```python
import forgesight
from forgesight_governance import BudgetInterceptor, PolicyInterceptor, KillSwitch

forgesight.configure(
    interceptors=[
        KillSwitch.from_config(),         # reads governance.kill_switch.*
        PolicyInterceptor.from_config(),  # reads governance.policies.*
        BudgetInterceptor.from_config(),  # reads governance.budgets.*
    ],
)
```

You can also construct them directly with the config models:

```python
from forgesight_governance import (
    BudgetInterceptor, BudgetCap, BudgetScope,
    PolicyInterceptor, PolicyRule, PolicyAction,
    KillSwitch, EnvKillSwitchSource, FileKillSwitchSource,
)

budget = BudgetInterceptor(
    caps=[
        BudgetCap(scope=BudgetScope.RUN, usd=5.0, tokens=2_000_000),
        BudgetCap(scope=BudgetScope.TEAM, key="growth", usd=200.0),
        BudgetCap(scope=BudgetScope.REPO, key="payments-agent", usd=50.0),
    ],
    on_breach="raise",        # "raise" | "drop" | "mark"
)

policy = PolicyInterceptor(
    rules=[
        PolicyRule(match={"environment": "prod"}, action=PolicyAction.DENY,
                   models=("*-experimental", "gpt-*-preview"),
                   reason="prod may only call GA models"),
        PolicyRule(match={"pii": "true"}, action=PolicyAction.REDACT),
        PolicyRule(match={"team": "growth"}, action=PolicyAction.ALLOW,
                   models=("claude-haiku-*", "gpt-*-mini")),
    ],
)

kill = KillSwitch(source=EnvKillSwitchSource())                 # env-var source
# or: KillSwitch(source=FileKillSwitchSource("/etc/forgesight/kill.txt", poll_seconds=5))
```

### YAML equivalent

```yaml
governance:
  budgets:
    per_run:
      usd: 5.0
      tokens: 2_000_000
    per_team:
      growth:   { usd: 200.0 }
      research: { usd: 2000.0 }
    per_repo:
      payments-agent: { usd: 50.0, tokens: 10_000_000 }
    per_environment:
      prod: { usd: 5000.0 }
    on_breach: "raise"          # "raise" | "drop" | "mark"

  policies:
    rules:
      - match: { environment: "prod" }
        action: "deny"
        models: ["*-experimental", "gpt-*-preview"]
        reason: "prod may only call GA models"
      - match: { pii: "true" }
        action: "redact"
      - match: { team: "growth" }
        action: "allow"
        models: ["claude-haiku-*", "gpt-*-mini"]

  kill_switch:
    source: "env"               # "env" | "file"
    file_path: null             # required when source == "file"
    poll_seconds: 5             # file source re-read interval
```

**Validation.** A `BudgetCap` must set at least one of `usd` / `tokens` (both null is an error); `on_breach` must be `raise|drop|mark`; a `PolicyRule` with `allow`/`deny` must set `models` (`redact` ignores models); `kill_switch.source: file` requires `file_path`.

## Behavior

Each interceptor acts **only on LLM-call records** (`record.llm is None` → it passes the record through untouched). When a control fires it raises a `GovernanceSignal` subclass; this is the **one sanctioned case** where an interceptor's exception is *not* swallowed by feat-008's isolation — it propagates to the caller by design. The scope maps that signal to a terminal `RunStatus`, the run is marked failed, and the run record still flushes.

- **`BudgetInterceptor`** — on each completed LLM record it adds the call's `cost_usd` / total tokens to the per-scope running totals (keyed on the run id for `RUN`, or on the `team`/`repo`/`environment` attribute for the others). If a projected total would breach a cap, it enforces `on_breach`: `raise` throws `BudgetExceeded` (a `GovernanceSignal`) → `RunStatus.BUDGET_EXCEEDED`; `drop` returns `None` (the record is dropped, run continues); `mark` flags the record with `forgesight.budget.exceeded=True` and lets the run continue. Totals are **process-local** (a shared cross-process store is a follow-up).
- **`PolicyInterceptor`** — first matching rule wins (the `match` predicate is an exact-equality test over the run's metadata). `deny` raises `PolicyDenied` → `RunStatus.GUARDRAIL` when the request model is in the rule's `models` set; `allow` raises `PolicyDenied` when the model is *not* in the allow-list; `redact` strips the captured content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`, etc.) and the LLM content from the record and passes it on.
- **`KillSwitch`** — checks the call's scope keys (`run` / `team` / `repo` / `environment`) against a `KillSwitchSource`; a tripped key raises `KillSwitchEngaged` → `RunStatus.BUDGET_EXCEEDED` so that scope halts while everything else keeps running. `EnvKillSwitchSource` reads `FORGESIGHT_KILL_<SCOPE>_<KEY>=true` per call (instant, no I/O). `FileKillSwitchSource` re-reads a `scope:key`-per-line trip list on a TTL (`poll_seconds`); a missing file fails open (nothing tripped).

The difference in one line: **budget** stops you spending too much, **policy** stops you calling the wrong model (or strips content), **kill-switch** stops a named scope on command.

## Operate it

### Trip a budget cap

1. Configure `governance.budgets.per_run.usd: 0.001` (a deliberately tiny cap) with `on_breach: "raise"`.
2. Run any agent that makes an LLM call carrying real cost.
3. Observe the run halt: a `BudgetExceeded` propagates to your caller, `run.status` is `RunStatus.BUDGET_EXCEEDED`, and the run record still exports normally. Catch and inspect the signal:

```python
from forgesight_governance import BudgetExceeded
try:
    with telemetry.agent_run("etl-agent", metadata={"team": "research"}) as run:
        ...
except BudgetExceeded as e:
    log.warning("halted: $%.4f > cap $%s (scope %s)", e.projected_usd, e.cap_usd, e.scope)
```

### Toggle the kill-switch

- **Env source:** export `FORGESIGHT_KILL_REPO_PAYMENTS_AGENT=true` (scope + key upper-cased, non-alphanumerics → `_`). The next LLM call for `repo=payments-agent` raises `KillSwitchEngaged`; every other repo keeps running. Unset it to restore.
- **File source:** add a line `repo:payments-agent` to the configured `file_path`. Within `poll_seconds` the source re-reads and that scope's runs trip; delete the line to clear.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Cap never trips | `governance.budgets.*` absent, or the run carries no matching `team`/`repo`/`environment` metadata | Configure the cap and ensure the run's `metadata={...}` carries the scope key the cap is keyed on. |
| `BudgetCap ... sets neither usd nor tokens` at bootstrap | Cap with both `usd` and `tokens` null | Set at least one; validation is fail-fast at `configure()`. |
| Per-team cap looks too low under load | Totals are **process-local**; multiple processes each accumulate separately | Expected in 0.3; a shared (Redis) store behind the same `BudgetCap` interface is a follow-up. |
| Policy `deny`/`allow` raises `must set models` | An allow/deny rule with no `models` set | Add the `models` tuple; only `redact` may omit it. |
| Kill-switch env var ignored | Wrong env-var name casing | Use `FORGESIGHT_KILL_<SCOPE>_<KEY>` with scope/key normalised (upper-cased, non-alphanumeric → `_`); value must be one of `1/true/yes/on`. |
| File kill-switch slow to take effect | TTL re-read interval | Lower `poll_seconds`; env source is instant if you need zero latency. |
| Backend missing the failed run record | (Should not happen) | The run record **still flushes** on a governance trip — telemetry is never lost; only the agent work halts. |
| Telemetry export failed but run continued | Exporter failure | Export is **non-blocking and fault-tolerant** — `export()` returns failure, never raises. This is the normal contract. **Governance is the deliberate exception:** a `GovernanceSignal` (`BudgetExceeded` / `PolicyDenied` / `KillSwitchEngaged`) is *meant* to stop the run — it is a control-plane decision, not a telemetry failure, so it propagates rather than being swallowed. |

## Reference

- Spec: [feat-020 — Cost budgets & governance](../features/feat-020-cost-budgets-and-governance.md)
- Package: [`packages/forgesight-governance`](../../packages/forgesight-governance) (`budget.py`, `policy.py`, `kill_switch.py`)
- Playbooks: [01 — Install](../playbooks/01-install.md), [02 — Instrument your agent](../playbooks/02-instrument-your-agent.md), [07 — Governance and budgets](../playbooks/07-governance-and-budgets.md)
