# feat-022: Agent registry, ownership & chargeback analytics

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-022 |
| **Title** | Agent registry, ownership & chargeback analytics — owned agents, auto-attached ownership metadata, cost rollups |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.4 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-registry` |
| **Depends on** | feat-002 (telemetry runtime), feat-010 (config/bootstrap), feat-006 (cost) |
| **Blocks** | none |

---

## 1. Why this feature

The SDK now emits, per run: cost (feat-006), business metadata (FR-5), and quality
(feat-021). FinOps can finally ask "what did we spend?" — and immediately hits a wall:
**who owns this spend, and which team gets the bill?** The cost is attributed to an
agent *name*; nobody has the mapping from name → team → repo → owner in a place the
telemetry can use.

Concrete scenarios that hit teams today:

- The monthly LLM bill is \$40,000. Finance asks each engineering team for their share.
  The cost telemetry has `agent.name` on every run, but the name→team mapping lives in
  someone's head, so chargeback is a manual reconstruction every month and it never
  fully balances.
- A run misbehaves at 3 a.m. The on-call sees `agent.name=invoice-parser` in the trace
  and has no idea who owns it, what its SLA tier is, or which repo it ships from. The
  ownership data exists in a service catalogue — a different system, not joined to the
  run.
- Every agent author *could* tag each run with `team=…`, `repo=…`, `owner=…` by hand,
  but they don't, or they spell it differently, so the `team` attribute is present on
  60% of runs and inconsistent on the rest. Chargeback on dirty dimensions is fiction.
- A platform team wants "cost by team by environment this quarter" and "the catalogue of
  every agent we run, with owner and lifecycle tier." Both are derivable from telemetry
  the SDK already emits + an ownership table — but there is no place the table lives
  that the SDK can read and stamp onto runs automatically.

This is the last mile from per-run telemetry to **org-level FinOps + ownership**: a
declared registry of agents whose ownership metadata is *auto-attached to every run*,
so cost rolls up by team/repo/environment cleanly and every run is traceable to a human.

## 2. Why this belongs in the SDK

- **The SDK is the only point that touches every run as it starts.** Auto-attaching
  ownership metadata means stamping `team` / `repo` / `owner` / `lifecycle_tier` onto a
  run *at run start*, from a single declared source, so it lands on every child span
  consistently (FR-5 propagation). Only the SDK is in that position. A side-car that
  joins ownership *after* export has dirty, late, per-backend data; stamping at source
  is clean and universal.
- **Cost attribution must key on the *same* metadata the SDK propagates.** Chargeback
  rollups are `sum(forgesight.usage.cost_usd)` grouped by the ownership dimensions
  (feat-006 emits the cost; FR-5 propagates the metadata). If the registry stamps the
  dimensions and the SDK already emits the cost on them, the rollup is just a
  group-by — no new data plane. Re-implementing this per agent means re-shipping the
  ownership table to every codebase and hoping they all spell `team` the same way.
- **Consistency is the entire value.** The reason chargeback fails today is dirty
  dimensions. A *declared registry* with one source of truth makes `team=platform`
  mean the same thing on every run from every agent — the invariant FinOps is buying.
  Leaving it to each agent guarantees drift (the same disease requirements §1.1 names
  for cost calc and quality, now for ownership).
- **It rides locked surfaces — no new SPI.** Ownership stamping is a run-start hook
  over the existing `TelemetryContext` metadata (feat-002); the registry source loads
  through the existing config/bootstrap (feat-010); the rollups read the existing cost
  attribute (feat-006). The registry is *data + a stamp*, not a new contract.
- **The anti-pattern if we don't:** ownership lives in a service catalogue the
  telemetry can't see, chargeback is a monthly manual join on dirty `team` tags, and
  on-call can't find an owner from a trace. The SDK has the cost and the run-start
  hook; not closing the loop wastes both.

This realises the **FinOps / governance persona** (requirements §5 — "cost attribution,
budgets, chargeback"), builds on FR-5 (business metadata) and FR-9 (cost), and is the
headline of roadmap **Phase 4 (registry & platform)**.

## 3. How consuming agents/teams benefit

**Before.** Chargeback is a monthly fire drill: export cost by `agent.name`, hand-join
to a name→team spreadsheet that's always stale, argue about the runs whose `team` tag
was missing or misspelled. On-call greps a trace, finds `agent.name`, and pings random
Slack channels to find the owner. Every agent author who remembers to tag ownership
does it differently.

**After.**

- **Day 0 — declare the agent once; every run is stamped.** Add a registry entry
  (`name`, `version`, `owner`, `team`, `repo`, `lifecycle_tier`, `sla_tier`). The SDK
  looks the running agent up by `(name, version)` at run start and auto-attaches the
  ownership fields as business metadata — on the root span and every child (FR-5).
  Agent authors write *zero* tagging code, and the tags are consistent by construction.
- **Day 7 — chargeback is a group-by, not a fire drill.** "Cost by team this month" =
  `sum(forgesight.usage.cost_usd)` grouped by `team` — a query the backend already
  supports because the SDK stamped clean `team` on every run. Same for `repo` and
  `environment`. The numbers balance because every run is attributed.
- **Day 14 — an agent catalogue, free from telemetry.** "Every agent we run, its owner,
  version, lifecycle tier, last-seen, run count, 30-day cost" — the registry (declared
  metadata) joined to the telemetry (observed activity) the SDK already emits. No new
  instrumentation.
- **Incident — owner in one hop.** The on-call reads `owner` / `team` / `sla_tier`
  straight off the trace, because the registry stamped it at run start. No spreadsheet,
  no Slack archaeology.
- **The win:** the loop closes. Per-run telemetry the team was already paying to emit
  (cost + metadata) becomes org-level FinOps (clean chargeback) and ownership (every
  run traceable to a human) by declaring an agent *once* instead of tagging it *every
  run, everywhere, inconsistently.*

## 4. Feature specifications

### 4.1 User-facing experience

```yaml
# agents.yaml — the declared registry (one source of truth)
agents:
  - name: invoice-parser
    version: "2.3.0"
    owner: "fin-platform@acme.com"
    team: "finance-platform"
    repo: "acme/invoice-agents"
    lifecycle: "ga"               # experimental | beta | ga | deprecated
    sla_tier: "tier-1"            # tier-1 | tier-2 | tier-3 | best-effort
  - name: nightly-summariser
    version: "*"                  # any version of this agent
    owner: "growth@acme.com"
    team: "growth"
    repo: "acme/summariser"
    lifecycle: "beta"
    sla_tier: "tier-3"
```

```python
# python — wire the registry once at bootstrap; runs are stamped automatically
import forgesight
from forgesight_registry import Registry

forgesight.configure(
    registry=Registry.from_file("agents.yaml"),    # or Registry.from_config()
)

# The agent author writes nothing extra — ownership is attached at run start:
from forgesight import telemetry

with telemetry.agent_run("invoice-parser", version="2.3.0") as run:
    ...   # run.metadata now carries team=finance-platform, owner=…, sla_tier=tier-1,
          # repo=…, lifecycle=ga — on the root span and every child.
```

```python
# python — chargeback + catalogue rollups over collected telemetry
from forgesight_registry import ChargebackReport, AgentCatalogue

report = ChargebackReport.from_records(records, registry=reg,
                                       dimensions=["team", "environment"])
for row in report.rows():
    print(row.team, row.environment, row.cost_usd, row.run_count, row.token_total)

catalogue = AgentCatalogue.from_records(records, registry=reg)
for entry in catalogue.entries():
    print(entry.name, entry.owner, entry.lifecycle, entry.last_seen, entry.cost_30d)
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
import { Registry } from '@agentforge/sdk-registry';

configure({ registry: Registry.fromFile('agents.yaml') });
```

The rollup helpers operate over exported records (e.g. from the in-memory testing
exporter, or a query result) — most teams will run the equivalent group-by *in their
backend* (the SDK stamped the clean dimensions; the backend does the math). The helpers
exist for offline reports, CI cost gates, and the catalogue view (see §9 on scope).

### 4.2 Public API / contract

```python
# forgesight_registry/model.py — experimental
from enum import Enum

class Lifecycle(str, Enum):
    EXPERIMENTAL = "experimental"; BETA = "beta"; GA = "ga"; DEPRECATED = "deprecated"

@dataclass(frozen=True, slots=True)
class AgentEntry:
    name: str
    version: str = "*"                    # exact version or "*" wildcard
    owner: str | None = None
    team: str | None = None
    repo: str | None = None
    lifecycle: Lifecycle = Lifecycle.GA
    sla_tier: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)   # arbitrary extra dimensions

class Registry:
    """The declared agent registry. Resolves (name, version) → AgentEntry and yields
    the ownership metadata stamped onto each run at run start."""
    @classmethod
    def from_file(cls, path: str) -> "Registry": ...         # YAML/JSON file
    @classmethod
    def from_config(cls) -> "Registry": ...                  # reads registry.* config
    @classmethod
    def from_source(cls, source: "RegistrySource") -> "Registry": ...  # pluggable

    def resolve(self, name: str, version: str | None) -> AgentEntry | None: ...
    def ownership_metadata(self, name: str, version: str | None) -> dict[str, str]: ...
```

```python
# forgesight_registry/source.py — experimental (pluggable, vendor-neutral)
@runtime_checkable
class RegistrySource(Protocol):
    """Where registry entries come from. File and HTTP shipped; custom via this Protocol."""
    def load(self) -> Sequence[AgentEntry]: ...
```

```python
# forgesight_registry/rollup.py — experimental
@dataclass(frozen=True, slots=True)
class ChargebackRow:
    dimensions: Mapping[str, str]         # e.g. {"team": "growth", "environment": "prod"}
    cost_usd: float
    run_count: int
    token_total: int
    failure_count: int

class ChargebackReport:
    @classmethod
    def from_records(cls, records: Sequence["Record"], *, registry: Registry,
                     dimensions: Sequence[str]) -> "ChargebackReport": ...
    def rows(self) -> Sequence[ChargebackRow]: ...
    def total_usd(self) -> float: ...

@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    name: str; version: str; owner: str | None; team: str | None
    lifecycle: Lifecycle; sla_tier: str | None
    last_seen: int | None; run_count: int; cost_30d: float

class AgentCatalogue:
    @classmethod
    def from_records(cls, records, *, registry: Registry) -> "AgentCatalogue": ...
    def entries(self) -> Sequence[CatalogueEntry]: ...
```

The registry stamps via the **locked** `TelemetryContext` metadata mechanism (feat-002)
and reads cost from the **locked** `forgesight.usage.cost_usd` attribute (feat-006) —
**no new SPI on the telemetry path.** `RegistrySource` is a new *package-local* Protocol
(not part of the locked `-api` four-SPI surface; it's a registry-loading detail). All
symbols are **experimental** within 0.x.

### 4.3 Internal mechanics

**Stamping at run start.** The registry installs a run-start hook (over feat-002's
existing context machinery — *not* a new SPI):

```
telemetry.agent_run(name, version)  enters
   │  bind TelemetryContext, generate run_id (feat-002)
   │
   ├── entry = registry.resolve(name, version)        # exact (name,version) → wildcard → None
   │      if entry: ctx.metadata.update(entry.ownership_metadata())
   │               # team, owner, repo, lifecycle, sla_tier, extra.* — caller-set keys win
   │
   └── open root span carrying the merged metadata → propagates to every child (FR-5)
```

**Resolution order:** exact `(name, version)` → `(name, "*")` wildcard → unregistered
(no stamp; counted in `sdk_registry_unmatched_total` so operators can find un-declared
agents — the registry doubles as a "what's running but undeclared" detector). A
**caller-set metadata key always wins** over a registry-stamped one, so a run can
override (e.g. a one-off `environment=staging`) without editing the registry.

**Chargeback rollup.** Pure aggregation over already-exported records — no live path:

```
records ──► group by the requested ownership dimensions (from stamped metadata)
        ──► per group: Σ forgesight.usage.cost_usd, Σ tokens, count runs, count failures
        ──► ChargebackRow[]   (and total_usd across all)
```

Because the dimensions were stamped *at source* from one declaration, the group-by is
clean: no missing `team`, no spelling drift. `environment` is the one dimension that
typically comes from deploy config rather than the registry; it's merged the same way.

**Catalogue = declared ∪ observed.** The catalogue joins the *declared* registry
entries (owner, lifecycle, SLA) with *observed* telemetry (last-seen, run count, 30-day
cost). It surfaces three states: declared-and-active, declared-but-silent (in the
registry, no recent runs — candidate for `deprecated`), and active-but-undeclared
(running, not in the registry — a governance gap). This is the closed loop: declaration
meets reality.

**Non-blocking & isolation (P6).** Stamping is an in-memory dict merge at run start —
no I/O, well within the NFR-1 hot-path budget. The registry source is loaded *once at
`configure()`* (with an optional TTL refresh for the HTTP source, best-effort, never
blocking a run — mirrors the cost-table refresh, cost-model §4.5). Rollups run offline
over records, off the hot path entirely.

### 4.4 Module packaging

- **`forgesight-registry`** is a new opt-in integration package (P2). It holds the
  `AgentEntry` / `Registry` model, the `RegistrySource` Protocol + file/HTTP sources,
  the run-start stamping hook, and the `ChargebackReport` / `AgentCatalogue` rollup
  helpers. It depends only on `-api` and `-core` — **no vendor SDK** (P1). The HTTP
  source uses the SDK's existing small HTTP dependency (the same one feat-006 refreshes
  the pricing table with), not a vendor client.

```bash
pip install forgesight-registry
```

```yaml
# forgesight.yaml
registry:
  enabled: true
  source: "file"
  path: "agents.yaml"
```

**Entry-point registration** under the SDK's module-load group so feat-010's bootstrap
wires the stamping hook automatically:

```toml
# forgesight-registry/pyproject.toml
[project.entry-points."forgesight.modules"]
registry = "forgesight_registry:install"

# Custom registry sources resolvable by name from config:
[project.entry-points."forgesight.registry_sources"]
file = "forgesight_registry.source:FileSource"
http = "forgesight_registry.source:HttpSource"
```

No telemetry-path entry point (exporter/interceptor) is added — the registry stamps
metadata through the existing context and reads cost through the existing attribute.

### 4.5 Configuration

```yaml
registry:
  enabled: true              # master switch (default: false — install does nothing until on)
  source: "file"             # "file" | "http" | "<custom-registered-name>"
  path: "agents.yaml"        # required when source == "file"
  url: null                  # required when source == "http"
  refresh_seconds: 300       # http source TTL refresh (best-effort, non-blocking); 0 = once
  stamp:
    # which AgentEntry fields to attach as run metadata, and under what key.
    fields: ["team", "owner", "repo", "lifecycle", "sla_tier"]
    prefix: ""               # optional namespace, e.g. "org." → org.team, org.owner
    caller_wins: true        # a caller-set metadata key overrides the registry stamp
  on_unmatched: "warn"       # "warn" | "ignore" | "error" — behaviour for undeclared agents

attribution:
  # default dimensions for chargeback rollups (overridable per call).
  dimensions: ["team", "repo", "environment"]
  cost_window_days: 30       # window for the catalogue's cost_30d column
```

**Validation rules.** `source: file` requires `path`; `source: http` requires `url`.
`stamp.fields` must name valid `AgentEntry` fields (or `extra.*` keys). `on_unmatched`
∈ `{warn, ignore, error}` — `error` is for strict shops that want *every* run to map to
a declared agent (fail-fast at run start); `warn` (default) stamps nothing and counts
the miss; `ignore` is silent. `attribution.dimensions` names metadata keys; an absent
dimension on a record groups it under `"<unattributed>"` so cost never silently
vanishes. Unknown keys rejected at `configure()` (architecture §8).

**Defaults.** `registry.enabled` defaults `false`; installing the package stamps
nothing until enabled (P2). `refresh_seconds` 300; `caller_wins` true; `on_unmatched`
warn; `attribution.cost_window_days` 30.

**Env overrides** (feat-010): `FORGESIGHT_REGISTRY_ENABLED`,
`FORGESIGHT_REGISTRY_SOURCE`, `FORGESIGHT_REGISTRY_PATH`, … kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

Add it later with `pip install forgesight-registry` + the `registry:` YAML and an
`agents.yaml`. No agent-code change: stamping is a bootstrap-installed run-start hook
over existing context, so every run starts carrying ownership the moment the registry
is wired. Remove it with `pip uninstall` + dropping the config; runs keep emitting
cost/structure unchanged, just without the auto-stamped ownership.

Upgrade safety: the feature rides the **locked** `TelemetryContext` metadata mechanism
(feat-002) and the **locked** `forgesight.usage.cost_usd` attribute (feat-006). New
optional `AgentEntry` fields or rollup dimensions land in minor bumps behind defaults
(P5). `RegistrySource` is a package-local Protocol; adding a shipped source (e.g. a
service-catalogue connector) is additive. The model/helpers are experimental within
0.x — signature changes are changelog-called-out, contracts beneath them do not move.

## 6. Cross-language parity

Identical across Python / TypeScript: the `agents.yaml` schema, the `AgentEntry`
fields, the resolution order (exact → wildcard → unmatched), the stamping rule
(`caller_wins`), the chargeback dimensions, and the catalogue's declared-∪-observed
shape. Allowed to differ: idiomatic naming (`fromFile` vs `from_file`), the HTTP-source
client, and the rollup helper surface (a TS runtime may lean on its own data tooling).
This feature is also the **0.4 TypeScript-parity milestone** (roadmap Phase 4), so
Python and TS land closer together than the 0.1–0.3 features.

## 7. Test strategy

- **Unit:** `(name, version)` resolution (exact > wildcard > unmatched);
  `ownership_metadata` field selection + `prefix`; `caller_wins` precedence; YAML/JSON
  file loading + schema validation; `on_unmatched` variants; chargeback group-by math
  (cost/token/run/failure sums, `<unattributed>` bucket); catalogue declared-∪-observed
  states.
- **Integration:** a run for a registered agent carries the stamped ownership on the
  root span *and* a child LLM span (FR-5 propagation, snapshot vs the in-memory
  exporter, feat-011); a run for an unregistered agent is unstamped and counted; a
  caller-set `environment` survives stamping.
- **Rollup:** a fixture of runs across two teams/two environments produces the expected
  `ChargebackRow`s and a `total_usd` that equals the sum of stamped costs; the catalogue
  flags a declared-but-silent and an active-but-undeclared agent.
- **Refresh:** the HTTP source TTL-refreshes without blocking a run; a failed refresh
  keeps the last-good registry (mirrors cost-table refresh).
- **Example:** a two-agent workspace with `agents.yaml`, producing a chargeback report
  and a catalogue, used as the headline demo.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Registry drifts from reality (undeclared agents running) | `on_unmatched` counts/warns/errors; the catalogue's active-but-undeclared state surfaces the gap as a feature, not a silent miss |
| `environment` isn't in the registry (it's a deploy concern) | Merged from caller metadata / deploy config the same way; `caller_wins` keeps registry and deploy data composable |
| Chargeback on a dimension absent from some runs | Absent dimension → `"<unattributed>"` bucket so cost never vanishes; surfaces coverage gaps |
| Registry file/URL unreachable at bootstrap | Loaded once at `configure()`; HTTP refresh is best-effort and keeps last-good (cost-table pattern) — a missing file at boot is a fail-fast config error, a failed *refresh* is not |
| Is the rollup the SDK's job, or the backend's? | The SDK *stamps clean dimensions*; rollups are mostly a backend group-by. The shipped helpers are for offline reports / CI cost gates / the catalogue — not a replacement for the backend (see §9) |
| Sensitive ownership data (owner emails) in traces | Ownership is business metadata; subject to the same redaction interceptor (feat-008) as any metadata if a team treats it as sensitive |

## 9. Out of scope

- **A registry UI / service-catalogue product.** The SDK reads a declared source and
  stamps runs; it does not host a catalogue web app (requirements §11 — emit, don't
  build dashboards). The `AgentCatalogue` helper produces data, not a UI.
- **Being the canonical source of truth for ownership.** It *reads* one (YAML/file/HTTP/
  custom source); integrating with an existing service catalogue (Backstage, etc.) is a
  custom `RegistrySource`, not core scope.
- **Live, streaming chargeback aggregation in-process.** Rollups are offline over
  exported records; continuous cost-by-team dashboards are the backend's job (the SDK
  stamped the clean dimensions to make that query trivial).
- **Budget enforcement.** Stopping spend by team/repo is feat-020 (which keys its caps
  on the same metadata this feature stamps); this feature is *attribution*, not control.
- **Multi-currency / billing-system integration.** Cost is USD (cost-model §3); pushing
  chargeback rows into a billing/ERP system is the caller's concern.
- **Agent deployment / lifecycle management.** The registry *records* lifecycle/SLA
  tier; it does not deploy, promote, or retire agents.

## 10. References

- [`../requirements.md`](../requirements.md) — FR-5 (business metadata), FR-9 (cost), §5 (FinOps persona)
- [`../design/cost-model.md`](../design/cost-model.md) — `forgesight.usage.cost_usd` (§4.4), refresh pattern (§4.5)
- [`../design/architecture.md`](../design/architecture.md) §3 (`TelemetryContext`, business metadata), §7 (lifecycle), §8 (failure modes)
- [`../design/design-principles.md`](../design/design-principles.md) — P1 (vendor-neutral), P2 (plug-and-play), P6 (non-blocking)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (metadata as namespaced attributes)
- feat-002 (runtime / metadata propagation), feat-010 (config/bootstrap), feat-006 (cost), feat-020 (budgets — same dimensions, enforcement side)
- Roadmap: features [`README.md`](./README.md) — Phase 4 (registry & TypeScript parity)
