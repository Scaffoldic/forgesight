# forgesight-registry

Declared agent **ownership** auto-stamped onto every run — plus offline **chargeback** and
**catalogue** rollups — for [ForgeSight](https://github.com/Scaffoldic/forgesight). The last
mile from per-run cost to org-level FinOps: declare an agent *once* instead of tagging it
every run, everywhere, inconsistently.

```bash
pip install forgesight-registry
```

```yaml
# agents.yaml — one source of truth
agents:
  - name: invoice-parser
    version: "2.3.0"
    owner: "fin-platform@acme.com"
    team: "finance-platform"
    repo: "acme/invoice-agents"
    lifecycle: "ga"
    sla_tier: "tier-1"
  - name: nightly-summariser
    version: "*"
    owner: "growth@acme.com"
    team: "growth"
    lifecycle: "beta"
```

```python
import forgesight
from forgesight_registry import Registry

reg = Registry.from_file("agents.yaml")
forgesight.configure(run_metadata_provider=reg.ownership_metadata)  # stamp ownership at run start

# Agent author writes nothing extra — every run now carries team/owner/repo/... on the
# root span and every child (FR-5):
with forgesight.telemetry.agent_run("invoice-parser", version="2.3.0") as run:
    ...
```

## How it works

- **Stamp at run start.** The registry resolves `(name, version)` — exact → `"*"` wildcard →
  unmatched — and merges the agent's ownership fields into the run's metadata. **Caller-set
  keys win** (a one-off `environment=staging` survives). Undeclared agents are counted
  (`on_unmatched` = `warn` | `ignore` | `error`), so the registry doubles as a "what's running
  but undeclared" detector.
- **Chargeback is a group-by.** `ChargebackReport.from_records(records, dimensions=["team", "environment"])`
  sums `cost_usd` / tokens / runs / failures per group — clean, because the dimensions were
  stamped at source. An absent dimension groups under `<unattributed>` so cost never vanishes.
- **Catalogue = declared ∪ observed.** `AgentCatalogue.from_records(records, registry=reg, now_unix_nanos=…)`
  joins the declared registry (owner / lifecycle / SLA) with observed telemetry (last-seen /
  run count / windowed cost), surfacing declared-but-silent and active-but-undeclared agents.

## Configuration

```yaml
registry:
  enabled: true              # master switch (default false — install does nothing until on)
  source: "file"             # file | http | <custom>
  path: "agents.yaml"
  on_unmatched: "warn"       # warn | ignore | error
  stamp:
    fields: ["team", "owner", "repo", "lifecycle", "sla_tier"]
    prefix: ""               # e.g. "org." → org.team, org.owner
```

Non-blocking: stamping is an in-memory dict merge (P6). No vendor SDK (P1) — the HTTP source
uses stdlib `urllib`. Attribution, not control — budget *enforcement* on these same dimensions
is `forgesight-governance` (feat-020).

## License

Apache-2.0
