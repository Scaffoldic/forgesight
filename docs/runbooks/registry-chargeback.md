# Registry & chargeback runbook

> Declare agent ownership once, auto-stamp it onto every run, then roll cost up by owner/team for chargeback and a catalogue. **Extra:** `pip install "forgesight[registry]"` · **Spec:** [feat-022](../features/feat-022-agent-registry-and-chargeback.md)

## What it does

`forgesight-registry` resolves an agent `(name, version)` to its declared ownership (owner, team, repo, lifecycle, SLA tier) and stamps those fields onto the run's metadata at run start — on the root span and every child, with caller-set keys winning. Because the dimensions are stamped *at source* from one declaration, downstream rollups group on clean fields with no missing or misspelled `team`. `ChargebackReport` aggregates cost/tokens/runs/failures by ownership dimensions; `AgentCatalogue` joins the *declared* registry with *observed* telemetry to surface declared-but-silent and active-but-undeclared agents.

## When to use it

- You need cost chargeback attributed to a team/owner without scraping or hand-labelling every run.
- You want every run traceable to a human and a repo for governance.
- You want to detect undeclared agents (a governance gap) or declared agents that have gone silent.

## Install

```bash
pip install "forgesight[registry]"   # the extra (pulls forgesight-registry)
pip install forgesight-registry      # standalone distribution, if you pin individually
```

## Set up / Configure

Declare your agents in YAML (or JSON — YAML is a superset), load a `Registry`, and wire its `ownership_metadata` as the run-start metadata provider:

```yaml
# agents.yaml
agents:
  - name: support-bot
    version: "1.4.0"          # exact version, or "*" to match any version
    owner: jordan@acme.io
    team: customer-success
    repo: acme/support-bot
    lifecycle: ga             # experimental | beta | ga | deprecated
    sla_tier: gold
    extra:                    # arbitrary extra fields, stamped verbatim
      cost_center: CC-204
  - name: research-agent
    version: "*"
    owner: sam@acme.io
    team: research
    lifecycle: beta
```

```python
import forgesight
from forgesight_registry import Registry

reg = Registry.from_file("agents.yaml")          # FileSource, read once
forgesight.configure(run_metadata_provider=reg.ownership_metadata)
```

Other constructors: `Registry.from_source(FileSource(...) | HttpSource(...))`, `Registry.from_entries([...])`, and `Registry.from_config(settings)` (driven by a `registry:` block — `enabled`, `source: file|http`, `path`/`url`, `on_unmatched`, `stamp.fields`, `stamp.prefix`). Sources ship as `FileSource` and `HttpSource` (stdlib `urllib`, no vendor SDK) and are also entry points (`forgesight.registry_sources`: `file`, `http`). The module entry point (`forgesight.modules`: `registry` → `forgesight_registry:install`) builds the registry from config and stashes it; wire it with `configure(run_metadata_provider=installed_registry().ownership_metadata)`.

`on_unmatched` controls what happens when an agent isn't declared: `warn` (default, logs and stamps nothing), `ignore`, or `error` (raises `RegistryUnmatched`). Like every module, an installed-but-not-`enabled` registry stamps nothing (P2).

## Behavior

- **Stamping.** At run start the runtime calls `ownership_metadata(name, version)`. Resolution is exact `(name, version)` → `(name, "*")` wildcard → unmatched (counted in `unmatched_count`; `on_unmatched` decides warn/ignore/error). The matched `AgentEntry.fields()` (owner, team, repo, lifecycle, sla_tier, plus `extra`) are merged into run metadata; an optional `stamp.fields` allowlist and `stamp.prefix` filter/namespace them. Caller-set keys take precedence.
- **Chargeback.** `ChargebackReport.from_records(records, dimensions=["team", "owner"])` groups exported records into `ChargebackRow`s (cost_usd, token_total, run_count, failure_count). Cost/tokens come from records carrying `record.llm`; run/failure counts come from `Kind.AGENT` records (failure = status not OK/RUNNING). An absent dimension groups under `"<unattributed>"` so cost never silently vanishes. `report.total_usd()` gives the grand total.
- **Catalogue.** `AgentCatalogue.from_records(records, registry=reg, now_unix_nanos=…, window_days=30)` unions declared entries with observed telemetry, yielding `CatalogueEntry`s with last_seen, run_count, windowed `cost_30d`, and `declared` / `active` flags — exposing declared-but-silent (`active=False`) and active-but-undeclared (`declared=False`) agents.

## Operate it

To verify stamping and produce a rollup:

1. Wire `configure(run_metadata_provider=reg.ownership_metadata)` and run a declared agent.
2. Inspect an exported run's metadata/attributes and confirm `owner`, `team`, `repo`, `lifecycle` (and any `prefix`) match `agents.yaml`. Check `reg.unmatched_count` stayed at 0.
3. Collect exported `Record`s and build a report:

   ```python
   from forgesight_registry import ChargebackReport, AgentCatalogue
   import time

   report = ChargebackReport.from_records(records, dimensions=["team", "owner"])
   for row in report.rows():
       print(row.dimensions, row.cost_usd, row.run_count, row.failure_count)
   print("total $", report.total_usd())

   catalogue = AgentCatalogue.from_records(records, registry=reg, now_unix_nanos=time.time_ns())
   for e in catalogue.entries():
       print(e.name, e.owner, e.declared, e.active, e.cost_30d)
   ```

4. Confirm undeclared agents show as `declared=False` and silent ones as `active=False`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Runs have no owner/team | Provider not wired, or registry not `enabled` | Pass `run_metadata_provider=reg.ownership_metadata` to `configure()`; set `registry.enabled: true` |
| `RegistryUnmatched` at run start | `on_unmatched: error` and agent not declared | Declare the agent (exact or `"*"`), or relax to `warn`/`ignore` |
| Cost lands under `<unattributed>` | The grouping dimension wasn't stamped on those records | Declare the agent and ensure the dimension is in `stamp.fields`; check `name`/`version` match |
| Wildcard not matching | Entry `version` isn't `"*"`, or an exact entry shadows it | Use `version: "*"`, or add the exact version |
| `ValueError: registry.source 'file' requires path` | `source: file` with no `path` | Set `registry.path` (or use `Registry.from_file(...)`) |
| Stamped keys collide with caller keys | Caller-set keys win by design | Use `stamp.prefix` to namespace registry fields |
| Empty chargeback/catalogue | No exported records, or none carry `llm`/`Kind.AGENT` | Flush before aggregating; aggregate over real exported runs |

## Reference

- Feature spec: [feat-022](../features/feat-022-agent-registry-and-chargeback.md)
- Package: [`packages/forgesight-registry`](../../packages/forgesight-registry)
- Cost model: [cost-model.md](../design/cost-model.md)
- Playbooks: [01-install.md](../playbooks/01-install.md) · [02-instrument-your-agent.md](../playbooks/02-instrument-your-agent.md)
