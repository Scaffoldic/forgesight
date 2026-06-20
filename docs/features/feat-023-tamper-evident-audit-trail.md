# feat-023: Tamper-evident audit trail & compliance export

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-023 |
| **Title** | Tamper-evident audit trail & compliance export — hash-chained, complete-capture audit record with a query/export surface |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-20 |
| **Target version** | 0.5 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-audit` |
| **Depends on** | feat-002 (runtime/metadata), feat-003 (async pipeline), feat-007 (event bus), feat-008 (interceptors/redaction), feat-006 (cost), feat-009 (errors) |
| **Blocks** | none |
| **Relates to** | feat-020 (policy/budget decisions — recorded, not enforced, here), feat-022 (ownership attribution) |

---

## 1. Why this feature

The SDK already emits, per run: structure (spans), cost (feat-006), errors
(feat-009), ownership (feat-022), and — where governance is installed — policy and
budget decisions (feat-020). That telemetry answers *"what happened?"* for an
operator looking at a dashboard. It does **not** answer the question a compliance
auditor or an incident reviewer actually asks, which is a different and harder one:
**"prove what this agent did, and prove the record of it wasn't altered."**

Ordinary telemetry fails that question in three specific ways:

- **It's deletable and editable, silently.** A trace store is mutable infrastructure:
  rows can be dropped, attributes can be rewritten, a window can be purged. When an
  auditor asks "is this the complete, unaltered record of what the `payments-approver`
  agent did on the 3rd?", the honest answer today is "probably — we have no way to
  *prove* nothing was removed." A span that was deleted leaves no hole; it leaves
  nothing.
- **It's sampled, so it has gaps a compliance review can't have.** The pipeline keeps
  10% of traces by default in many deployments (feat-003 `sample_rate`) — exactly the
  right call for cost/perf observability, and exactly wrong for governance. If a
  regulator asks "show me every model call this agent made against EU customer data,"
  "we kept a representative 1-in-10 sample" is not an answer. The governance-relevant
  events must be captured *completely*, even when the trace they belong to was sampled
  out.
- **It has no compliance query/export surface.** "Export every action by principal
  `clinician-bot`, attributed to its owning team, with cost, for Q2, as a signed
  bundle for the auditor" is a first-class governance request. It is *derivable* from
  the telemetry the SDK emits — but a trace backend's ad-hoc UI query is not an audit
  export, and the data the auditor needs (attributed, complete, integrity-checkable)
  isn't shaped for it.

Concretely, three scenarios hit governance-minded teams the first month an agent
touches regulated data:

- An incident review opens after the `invoice-approver` agent auto-approved a
  $40,000 invoice it shouldn't have. The reviewer needs the *complete* sequence —
  run start, every model call, the tool call that hit the ledger, the policy decision
  that let it through, run end — each attributed to the agent and its owner, each
  stamped with cost, and a guarantee that **no step was quietly removed** between the
  incident and the review. The sampled trace store has a 1-in-10 chance of having kept
  the run at all.
- A SOC-2 / HIPAA auditor asks for "the audit log for `clinician-bot` for March, and
  proof it's intact." Handing over a database export proves nothing — the auditor's
  next question is "how do I know you didn't delete the embarrassing rows?"
- Six months after the fact, legal needs to reconstruct exactly what an agent did in
  one run and demonstrate the record predates the dispute. The trace was sampled out
  on day one and is simply gone.

This feature is a **governance-grade projection of the telemetry the SDK already
emits**. The SDK stays the single emission path; this adds a *second projection*
alongside the exporters — an append-only, hash-chained **audit record** with the
three things ordinary telemetry lacks: **integrity** (each event carries
`prev_hash`/`hash`; a `verify()` walks the chain so deletion or alteration is
detectable), **complete capture** (audit-relevant events are recorded even when the
trace is sampled), and a **compliance query/export surface** (by principal / team /
kind / time, with cost rollups and a bundle export).

## 2. Why this belongs in the SDK

- **The SDK is the single point that sees every audit-relevant moment as it happens.**
  Run start, every model call, every tool call, every error, and (where governance is
  installed) every policy/budget decision already flow through the runtime (feat-002)
  and the event bus (feat-007). Building the audit record *at source* means it is
  complete and attributed by construction. A side-car that reconstructs an audit log
  *from exported traces* inherits the trace store's sampling gaps and mutability — it
  can never be more complete or more tamper-evident than the lossy thing it reads.
- **Complete capture requires a sampling override only the runtime can apply.** "Keep
  10% of traces for cost, but 100% of governance events for compliance" is a decision
  about a record *as it is produced*, before the head-sampling drop (feat-003 §4.3).
  Only the in-process runtime is upstream of that drop. Bolt this on after export and
  the events you most need are the ones already discarded.
- **Redaction must run once, before the audit record is written — same chain, same
  guarantee (P7).** An audit log of prompts-with-PII is itself a liability. The
  interceptor chain (feat-008) already redacts every record once, before fan-out. The
  audit sink rides *downstream of that chain*, so the same `PIIRedactionInterceptor`
  that scrubs the exporters scrubs the audit record — one redaction, every projection.
  A separate audit path would be a second place to get redaction wrong.
- **Integrity is a framework invariant, not a per-agent chore.** "Each event hashes
  the previous event's hash" is a subtle, get-it-exactly-right property: a stable
  canonical serialization, a defined hash over defined fields, a `verify()` that walks
  the whole chain. Done once in framework code and proven by a conformance suite (P10),
  every agent inherits a tamper-evident log. Done per agent, it's wrong the first time
  someone reorders a field.
- **It rides locked surfaces — no new SPI on the `-api` four.** The audit sink consumes
  the same `Record`s the exporters consume (feat-001/003), subscribes to the same
  `LifecycleEvent`s (feat-007), reads the same `forgesight.usage.cost_usd` (feat-006)
  and `error.type` (feat-009), and reuses ownership stamped by feat-022. `AuditSink` is
  a **package-local Protocol** with its own conformance suite — a sink, like an
  exporter, *not* a fifth entry on the locked SPI surface (P5).
- **The anti-pattern if we don't:** every regulated team hand-rolls an audit listener
  (feat-007 §1 literally names "write an immutable audit row for every run, for
  compliance" as a motivating case), each with a different event taxonomy, no integrity
  chain, no sampling override (so the log has the same gaps as the trace store), and no
  export surface — and none of them can answer the auditor's "prove it wasn't altered."

This realises the **FinOps / governance persona** (requirements §5) and the
governance thread the SDK exists to own (requirements §1.1 — "run identity,
governance"). It builds directly on FR-5 (metadata), FR-7 (errors), FR-9 (cost),
FR-8 (events), and FR-10 (interception), and is a headline of **Phase 5 (governance &
compliance)**.

## 3. How consuming agents/teams benefit

**Before.** Compliance asks for an audit trail; a team writes a custom `EventListener`
(feat-007) that appends a row per run to a database. It has no integrity chain (rows
are editable and the team can't prove otherwise), it inherits the trace pipeline's
sampling (the log is missing ~90% of runs in a sampled deployment), the event shapes
are bespoke (so two teams' audit logs are incomparable), and there's no export — an
auditor request becomes a SQL session and a leap of faith. When the auditor asks "how
do you know nothing was deleted?", there is no answer.

**After.**

- **Day 0 — turn it on; every audit-relevant event is recorded, complete and
  attributed.** `pip install forgesight-audit` + an `audit:` config block. The runtime
  records `run.start`, `model.call`, `tool.call`, `error`, and (where feat-020 is
  installed) `policy.decision` / `budget.event` as `AuditEvent`s — *even when the run's
  trace was sampled out* — each attributed to the agent and its owner (feat-022) and
  stamped with cost (feat-006). Agent authors write zero audit code.
- **Day 1 — the log proves its own integrity.** Each `AuditEvent` carries
  `prev_hash` + `hash`. `forgesight-audit verify <log>` walks the chain end to end; a
  deleted, reordered, or altered event breaks the chain and `verify()` returns the
  exact index where it broke. "Prove it wasn't altered" becomes one command.
- **Day 7 — the compliance query is a first-class surface, not a SQL leap of faith.**
  "Every action by `clinician-bot`, by owning team, with cost, for March" is
  `AuditQuery(principal=…, since=…, until=…)` against the sink, returning attributed,
  cost-stamped, integrity-verified events — and `export()` packages them as a bundle
  (JSONL + a manifest with the head hash) for the auditor.
- **Incident — the complete, attributed, tamper-evident record in one place.** The
  reviewer of the $40k auto-approval reads the full ordered sequence for that one run:
  start → model calls → the ledger tool call → the `policy.decision` that allowed it →
  end, each with owner and cost, with a verified chain proving nothing between the
  events was removed. No 1-in-10 gamble on whether the trace survived sampling.
- **Bridge to the observability stack you already run.** The `otel` sink emits each
  `AuditEvent` as a proper OTel **log record**, so the audit projection lands in the
  same backend as the traces (correlated by `run_id` / `trace_id`) without inventing a
  parallel store — and the `siem` export ships the same events to a SIEM/syslog
  collector for teams whose compliance tooling lives there.
- **The win:** telemetry the team already emits becomes a tamper-evident, complete,
  attributed, queryable compliance record by installing one package — instead of
  hand-rolling an audit listener that can't prove its own integrity and shares the
  trace store's gaps.

## 4. Feature specifications

### 4.1 User-facing experience

```yaml
# forgesight.yaml — turn on the audit projection (opt-in; P2)
audit:
  enabled: true
  sink: "jsonl"                  # jsonl (default) | sqlite | otel | siem | <custom>
  path: "audit/agent-audit.jsonl"   # jsonl/sqlite sinks: append-only, hash-chained
  capture:
    # the audit-relevant AuditEvent kinds to record (the taxonomy, §4.2)
    kinds: ["run.start", "run.end", "model.call", "tool.call", "error",
            "policy.decision", "budget.event"]
    override_sampling: true      # record these EVEN WHEN the trace was sampled out
  redact: true                   # ride the interceptor chain's redaction before write (P7)
```

```python
# python — wiring is one line at bootstrap; recording is automatic thereafter
import forgesight
from forgesight_audit import JsonlAuditSink

forgesight.configure(
    sample_rate=0.1,                       # 10% of traces kept for observability (feat-003)
    audit_sink=JsonlAuditSink("audit/agent-audit.jsonl"),  # but 100% of audit events recorded
)

# The agent author writes NOTHING extra — audit events are recorded at source:
from forgesight import telemetry

with telemetry.agent_run("payments-approver", version="3.1.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        call.record_usage(input=1200, output=300)     # → AuditEvent(kind="model.call", cost=…)
    run.tool_call(name="ledger.post", arguments={...}) # → AuditEvent(kind="tool.call")
# → run.start / model.call / tool.call / run.end recorded as a hash-chained sequence,
#   attributed to payments-approver + its owner (feat-022), even if this trace was sampled out.
```

```python
# python — verify integrity, then query/export for an auditor (offline; off the hot path)
from forgesight_audit import JsonlAuditSink, AuditQuery, verify

sink = JsonlAuditSink("audit/agent-audit.jsonl")

result = verify(sink)                       # walks prev_hash/hash over the whole chain
assert result.intact, f"chain broke at index {result.broken_at}: {result.reason}"

q = AuditQuery(principal="clinician-bot", since=MARCH_1, until=APRIL_1)
report = sink.query(q)
print(report.event_count, report.cost_usd_total)            # attributed cost rollup
for ev in report.events():
    print(ev.kind, ev.principal, ev.owner, ev.cost_usd, ev.timestamp_unix_nanos)

sink.export(q, to="audit/clinician-bot-2026-03.bundle")     # JSONL + manifest(head_hash)
```

```typescript
// typescript (parity sketch — targets 0.5/0.6)
import { configure } from '@agentforge/sdk';
import { JsonlAuditSink, AuditQuery, verify } from '@agentforge/sdk-audit';

configure({ sampleRate: 0.1, auditSink: new JsonlAuditSink('audit/agent-audit.jsonl') });

const sink = new JsonlAuditSink('audit/agent-audit.jsonl');
const { intact, brokenAt } = verify(sink);
const report = sink.query(new AuditQuery({ principal: 'clinician-bot', since, until }));
await sink.export(new AuditQuery({ principal: 'clinician-bot' }), 'out.bundle');
```

The query/export helpers operate over the recorded audit log (a file, a SQLite db, or
whatever a custom sink backs). They exist for offline compliance reports, auditor
bundles, and CI integrity gates — not as a log-search UI (see §9).

### 4.2 Public API / contract

```python
# forgesight_audit/model.py — experimental (within 0.x)
from enum import StrEnum

class AuditKind(StrEnum):
    """The stable AuditEvent taxonomy. Open set: new kinds appended in a minor (P5);
    consumers ignore kinds they don't know."""
    RUN_START = "run.start"
    RUN_END = "run.end"
    MODEL_CALL = "model.call"
    TOOL_CALL = "tool.call"
    ERROR = "error"
    POLICY_DECISION = "policy.decision"     # only when feat-020 is installed
    BUDGET_EVENT = "budget.event"           # only when feat-020 is installed

@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only, hash-chained audit record. Attributed + cost-stamped + chained."""
    kind: AuditKind
    seq: int                                # monotonic position in the chain (0-based)
    timestamp_unix_nanos: int
    run_id: str                             # the owning AgentRun (ULID, feat-001)
    trace_id: str
    principal: str                          # the acting agent (agent.name)
    version: str | None = None              # agent.version
    owner: str | None = None                # from feat-022 ownership stamp, when present
    team: str | None = None
    cost_usd: float | None = None           # forgesight.usage.cost_usd, where known (feat-006)
    status: str | None = None               # RunStatus on run.end / error
    attributes: Mapping[str, str] = field(default_factory=dict)  # forgesight.* namespaced (§4.3)
    prev_hash: str | None = None            # hash of the predecessor (None for seq 0)
    hash: str = ""                          # hash over the canonical serialization of THIS event

@dataclass(frozen=True, slots=True)
class AuditQuery:
    principal: str | None = None
    team: str | None = None
    kind: AuditKind | None = None
    since: int | None = None                # unix nanos, inclusive
    until: int | None = None                # unix nanos, exclusive

@dataclass(frozen=True, slots=True)
class VerifyResult:
    intact: bool
    event_count: int
    broken_at: int | None = None            # seq index where the chain first failed, else None
    reason: str | None = None               # "altered" | "deleted" | "reordered" | None
```

```python
# forgesight_audit/sink.py — experimental, PACKAGE-LOCAL Protocol (NOT a fifth -api SPI)
@runtime_checkable
class AuditSink(Protocol):
    """A second projection of telemetry, alongside the exporters. Writes append-only,
    hash-chained AuditEvents. Like an exporter: NEVER raises into the run, NEVER blocks
    the hot path (P6). Drivers: jsonl, sqlite, otel, siem; custom via this Protocol."""
    def write(self, event: AuditEvent) -> None: ...          # append; build done upstream; non-raising
    def query(self, q: AuditQuery) -> "AuditReport": ...     # offline; off the hot path
    def export(self, q: AuditQuery, to: str) -> None: ...    # bundle: JSONL + manifest(head_hash)
    def head_hash(self) -> str | None: ...                   # the latest chain hash (for anchoring)
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...

class AuditReport:                                            # experimental
    def events(self) -> Sequence[AuditEvent]: ...
    @property
    def event_count(self) -> int: ...
    @property
    def cost_usd_total(self) -> float: ...                   # Σ cost_usd over matched events

def verify(sink: AuditSink) -> VerifyResult:                 # experimental
    """Walk prev_hash/hash over the whole chain; detect deletion / alteration / reorder."""
```

```python
# forgesight_audit/sinks/ — the shipped drivers (experimental)
class JsonlAuditSink:   ...   # default: append-only, hash-chained JSONL file
class SqliteAuditSink:  ...   # append-only, hash-chained rows in SQLite (indexed query)
class OtelAuditSink:    ...   # emits each AuditEvent as an OTel LOG RECORD (bridge, P4)
class SiemAuditSink:    ...   # SIEM/syslog-style export (generic; not a vendor client, P1)
```

The sink consumes the **locked** `Record` (feat-001) and subscribes to the **locked**
`LifecycleEvent`/`EventType` (feat-007); it reads cost from the **locked**
`forgesight.usage.cost_usd` (feat-006) and error from the **locked** `ErrorInfo` /
`error.type` (feat-009); ownership comes from feat-022's stamp; policy/budget come
from feat-020's `GovernanceSignal`-tagged records. **No new SPI lands on the `-api`
four-Protocol surface (P5).** `AuditSink` is a *package-local* Protocol with its own
conformance suite (P10, §7). All `forgesight_audit` symbols are **experimental**
within 0.x; the `prev_hash`/`hash`/canonical-serialization contract is the one part
treated as stable-from-ship (changing it would invalidate existing chains — §5).

### 4.3 Internal mechanics

**One event → two projections.** The SDK stays the single emission path. A produced
record fans out to the exporters *and* — independently, isolated — to the audit sink.
The audit projection has its own sampling rule and rides downstream of the same
interceptor (redaction) chain:

```
                          telemetry.agent_run(...) / llm_call / tool_call / error  (feat-002)
                                          │  build immutable Record (feat-001)
                                          ▼
                          interceptor chain  ── redact / gate content (feat-008, P7)
                                          │   Record | None (None ⇒ dropped, no audit event)
            ┌─────────────────────────────┴─────────────────────────────┐
            ▼                                                             ▼
   head sampling (feat-003)                                  audit tap (feat-023)
   sample_rate, e.g. 0.10                                    override_sampling: record
   unsampled ⇒ dropped                                       audit-relevant kinds at 100%
            │                                                             │
            ▼                                                             ▼
   bounded queue → worker → EXPORTERS                        build AuditEvent  (attribute,
   (OTLP, Langfuse, …)   [PROJECTION 1: spans]                 cost-stamp, redacted already)
                                                                          │
                                                                          ▼
                                                              chain + write  [PROJECTION 2]
```

The audit tap sits **after the interceptor chain** (so the redacted record is what
gets recorded — P7) and **beside head sampling** (so a sampled-out trace still yields
audit events when `override_sampling` is on — complete capture). Both are downstream
of the *same* record build, so the two projections never disagree: a field redacted
for the exporters is redacted in the audit log; a record vetoed by an interceptor
(returned `None`) produces no span *and* no audit event.

**The hash chain (integrity).** Each `AuditEvent` is chained to its predecessor:

```
seq 0:   prev_hash = None
         hash      = H( canonical(event_0_fields) )
seq 1:   prev_hash = hash(event_0)
         hash      = H( canonical(event_1_fields) || prev_hash )
seq n:   prev_hash = hash(event_{n-1})
         hash      = H( canonical(event_n_fields) || prev_hash )
```

`H` is SHA-256 over a **canonical serialization** (sorted keys, fixed field order,
UTF-8, no incidental whitespace) of the event's content fields *plus* `prev_hash`.
Because each hash folds in the previous one, the chain is a Merkle-style linked list:
altering event *k* changes `hash(k)`, which breaks `prev_hash(k+1)`, which breaks
every hash after it. `verify()` recomputes the chain and reports the first `seq` where
the recorded `hash`/`prev_hash` disagrees with the recomputation:

```
verify(sink):
   prev = None
   for ev in sink (in seq order):
       expect = H( canonical(ev.content) || prev )
       if ev.prev_hash != prev:  return VerifyResult(False, broken_at=ev.seq, reason="reordered|deleted")
       if ev.hash != expect:     return VerifyResult(False, broken_at=ev.seq, reason="altered")
       prev = ev.hash
   return VerifyResult(True, event_count=…)
```

A **deleted** event leaves a `prev_hash` that points at a hash no surviving event
produces → break. An **altered** event recomputes to a different `hash` → break. A
**reordered** event has a `prev_hash` that doesn't match the actual predecessor →
break. Silent tampering is detectable; that is the whole point. (What this does *not*
defend against — an attacker who rewrites the *entire* chain from a chosen point with
consistent hashes — is addressed by **head-hash anchoring** in a later phase, §9: the
sink exposes `head_hash()` so an external notary can periodically sign it.)

**Complete capture (the sampling override).** Head sampling (feat-003 §4.3) drops a
whole trace at the root for cost/perf. The audit tap takes its own decision: for the
configured `capture.kinds`, when `override_sampling` is true, the `AuditEvent` is built
and written *regardless of the trace's sampling decision*. So a deployment runs
`sample_rate=0.1` for its dashboards and still has a 100%-complete governance log. The
override applies only to the audit-relevant kinds — not arbitrary spans — so the audit
log stays a governance record, not a second full trace store (footprint, NFR-6).

**Attribution & cost stamping.** Each `AuditEvent` is built from the post-interceptor
record: `principal`/`version` from the run's `agent.name`/`agent.version`;
`owner`/`team` from feat-022's stamped ownership metadata (absent if the registry
isn't wired — the field is simply `None`); `cost_usd` from `forgesight.usage.cost_usd`
(feat-006) on `model.call` and summed onto `run.end`; `status`/error from feat-009 on
`run.end`/`error`. `policy.decision` / `budget.event` are emitted only when feat-020
is installed, built from the `GovernanceSignal`-carrying record (the decision is
*recorded* here — enforcement is feat-020's; §9).

**Non-blocking & fault isolation (P6).** `write()` is off the hot path and contractually
**never raises** — exactly the exporter contract (feat-003 §4.4). The audit projection
rides the existing async pipeline: building + chaining + writing happen on the worker
side, not on the agent task. A sink that's slow, full, errors, or is misconfigured is
caught, counted (`sdk_audit_write_failures_total`), and isolated — it never breaks the
run, the exporters, or sibling sinks. The chain's `seq`/`prev_hash` are assigned in the
single-writer worker, so ordering is well-defined without locking the hot path. Query
and export run **offline** over the recorded log, off the hot path entirely.

### 4.4 Module packaging

- **`forgesight-audit`** is a new opt-in package (P2). It holds the `AuditEvent` /
  `AuditKind` / `AuditQuery` model, the package-local `AuditSink` Protocol + `verify()`,
  the audit tap that builds events from post-interceptor records, and the four shipped
  drivers (`jsonl`, `sqlite`, `otel`, `siem`). It depends on `-api` + `-core` only —
  **no backend/model-provider SDK** (P1). The `otel` sink uses the OTel SDK's log API
  (already a `-core` dependency, feat-003), not a vendor client; the `siem` sink writes
  syslog/JSON-lines to a generic collector endpoint, not a branded client.

```bash
pip install forgesight-audit
```

```yaml
# forgesight.yaml
audit:
  enabled: true
  sink: "jsonl"
  path: "audit/agent-audit.jsonl"
```

**Entry-point registration** — `forgesight.modules` (so feat-010's bootstrap wires the
audit tap), and `forgesight.audit_sinks` (so drivers resolve by name from config):

```toml
# forgesight-audit/pyproject.toml
[project.entry-points."forgesight.modules"]
audit = "forgesight_audit:install"

# Audit-sink drivers resolvable by name from the audit.sink config key:
[project.entry-points."forgesight.audit_sinks"]
jsonl  = "forgesight_audit.sinks.jsonl:JsonlAuditSink"
sqlite = "forgesight_audit.sinks.sqlite:SqliteAuditSink"
otel   = "forgesight_audit.sinks.otel:OtelAuditSink"
siem   = "forgesight_audit.sinks.siem:SiemAuditSink"
```

The audit tap subscribes to the existing event/record machinery (feat-007/003) and
reads existing attributes (cost, error, ownership) — it adds **no exporter and no
interceptor**, so it composes with whatever telemetry backends are already wired. A
custom sink ships its own package and registers under `forgesight.audit_sinks`.

### 4.5 Configuration

```yaml
audit:
  enabled: false               # master switch (default: false — install does nothing until on; P2)
  sink: "jsonl"                # "jsonl" | "sqlite" | "otel" | "siem" | "<custom-registered-name>"
  path: "audit/agent-audit.jsonl"   # required for jsonl/sqlite (append-only, hash-chained)
  endpoint: null               # required for siem (syslog/collector URL); ignored otherwise
  capture:
    kinds:                     # which AuditKinds to record (the taxonomy)
      ["run.start", "run.end", "model.call", "tool.call", "error",
       "policy.decision", "budget.event"]
    override_sampling: true    # record audit kinds even when the trace was head-sampled out
  redact: true                 # write the post-interceptor (redacted) record (P7); false = raw (discouraged)
  hash_algorithm: "sha256"     # the chain hash; named, not a literal (P8)
```

**Validation rules.** `sink: jsonl|sqlite` requires `path`; `sink: siem` requires
`endpoint`; `sink: otel` requires the OTel exporter (feat-004) to be configured (the
log records need somewhere to go) — else fail-fast at `configure()` (architecture §8).
`capture.kinds` must name valid `AuditKind`s; `policy.decision`/`budget.event` are
silently no-ops if feat-020 isn't installed (nothing emits them). `hash_algorithm` ∈
`{sha256, sha512}` (named, P8). `redact: false` is accepted but logs a WARN at
`configure()` — an un-redacted audit log is a liability (P7). Unknown keys rejected at
`configure()`.

**Defaults.** `audit.enabled` defaults `false`; installing the package records nothing
until enabled (P2). `sink` `jsonl`; `capture.override_sampling` `true` (complete
capture is the point); `redact` `true`; `hash_algorithm` `sha256`.

**Env overrides** (feat-010): `FORGESIGHT_AUDIT_ENABLED`, `FORGESIGHT_AUDIT_SINK`,
`FORGESIGHT_AUDIT_PATH`, `FORGESIGHT_AUDIT_ENDPOINT`,
`FORGESIGHT_AUDIT_OVERRIDE_SAMPLING`, `FORGESIGHT_AUDIT_REDACT` — kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

Add it later with `pip install forgesight-audit` + the `audit:` YAML. No agent-code
change: the tap is a bootstrap-installed subscriber over the existing record/event
machinery, so every run starts producing audit events the moment the sink is wired —
including completely, past the sampler. Remove it with `pip uninstall` + dropping the
config; runs keep emitting traces/cost/metrics unchanged, just without the parallel
audit projection. Swap drivers (jsonl → sqlite → otel → siem) with one config line; the
`AuditEvent` taxonomy and chain contract are identical across drivers.

Upgrade safety: the feature rides the **locked** `Record` (feat-001), `LifecycleEvent`
(feat-007), `forgesight.usage.cost_usd` (feat-006), and `error.type` (feat-009) — no
new `-api` SPI. `AuditSink` is a package-local Protocol; adding a shipped driver is
additive. Most `forgesight_audit` symbols are experimental within 0.x (signature
changes are changelog-called-out). The **exception** is the chain contract — the
canonical serialization + `prev_hash`/`hash` rule: changing it would invalidate every
existing log's `verify()`, so it is treated as stable-from-ship and versioned
(`schema_version` on each event) with a migration path, never silently altered (P5).

## 6. Cross-language parity

Identical across Python / TypeScript: the `AuditKind` taxonomy, the `AuditEvent` fields,
the **canonical serialization + SHA-256 chain rule** (a Python-written log must
`verify()` in TS and vice-versa — the byte-exact serialization is the contract), the
`override_sampling` complete-capture semantics, the redaction-before-write rule, the
`AuditQuery` shape and the cost rollup, and the `verify()` break semantics
(altered/deleted/reordered). Allowed to differ: idiomatic naming (`force_flush` vs
`forceFlush`, `prev_hash` vs `prevHash`), the SQLite/file driver internals, and the
OTel-log emission primitive. Because the chain is cross-verifiable, the canonical
serialization is specified down to byte order (the one place parity is byte-exact, not
just semantic). Python lands first (0.5); TS targets parity on its 0.6 line.

## 7. Test strategy

- **Chain integrity (the headline):** a recorded log of N events `verify()`s intact;
  altering one event's field breaks `verify()` at exactly that `seq` with
  `reason="altered"`; deleting an event breaks at the gap with `reason="deleted"`;
  swapping two events breaks with `reason="reordered"`. The first-break index is exact.
- **Complete capture:** with `sample_rate=0.1` and `override_sampling=true`, a run whose
  trace is sampled *out* (no spans exported) still produces its full `run.start … run.end`
  audit sequence; with `override_sampling=false`, audit events follow the trace's
  sampling decision.
- **Redaction-before-write (P7):** a secret in a record (key-redacted by feat-008) is
  `<redacted>` in the written `AuditEvent` too; an interceptor-vetoed record (`None`)
  produces no audit event (parity with no-span).
- **Attribution & cost:** `model.call` events carry `forgesight.usage.cost_usd`;
  `run.end` carries the summed run cost and the `RunStatus`; with feat-022 wired,
  `owner`/`team` are stamped; without it, they're `None` (not an error).
- **Governance kinds:** with feat-020 installed, a denied policy emits a
  `policy.decision` event and a budget trip a `budget.event`, built from the
  `GovernanceSignal` record; without feat-020, those kinds simply never emit.
- **Non-blocking & isolation (P6):** a sink whose `write()` raises / hangs / is
  misconfigured never fails or stalls the run or the exporters;
  `sdk_audit_write_failures_total` ticks for the bad sink only.
- **Query/export:** an `AuditQuery` by principal+time returns the matching events with
  the correct `cost_usd_total`; `export()` produces a JSONL bundle + a manifest carrying
  the `head_hash`; the exported bundle `verify()`s intact.
- **Drivers + conformance:** `jsonl`, `sqlite`, `otel`, `siem` each pass
  `run_audit_sink_conformance` (P10, feat-011-style suite local to `forgesight-audit`):
  append-only, never-raises-on-write, chain-correct, `verify()`-consistent,
  flush/shutdown idempotent. The `otel` sink additionally asserts a well-formed OTel
  **log record** per event (P4 — no invented attributes; custom fields namespaced
  `forgesight.*`).
- **Example:** a regulated two-agent workspace running `sample_rate=0.1` with the jsonl
  sink, producing a complete hash-chained log, a `verify()` gate in CI, and an auditor
  bundle export — the headline demo.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Hash-chain detects but doesn't *prevent* a full-rewrite by someone with write access | v1 is integrity-evidence (tamper-*evident*), not prevention. `head_hash()` is exposed so an external notary can periodically sign/anchor the head; cryptographic signing/anchoring is an explicit later phase (§9). |
| Cross-language `verify()` requires byte-exact serialization | The canonical serialization is specified down to key order / encoding / separators and is the one byte-exact parity contract (§6); a conformance vector (a fixed log + expected head hash) is shared across language test suites. |
| Complete capture inflates audit-log volume vs a sampled trace store | The override applies only to the configured audit-relevant `kinds`, not all spans (NFR-6); `sqlite`/`siem` drivers handle volume; `capture.kinds` is tunable to the team's compliance scope. |
| An un-redacted audit log leaks PII (a worse liability than a trace) | `redact: true` by default; the sink writes the *post-interceptor* record (P7); `redact: false` is accepted but WARNs at `configure()`. |
| Audit write on the hot path would reintroduce blocking | `write()` is off the hot path on the worker side, contractually non-raising, fault-isolated (P6) — same contract as an exporter; query/export are offline. |
| Is `AuditSink` a fifth `-api` SPI? | No. It's a *sink* (a projection), package-local to `forgesight-audit`, with its own conformance suite — the locked `-api` surface stays at four (P5, P10). |
| Does this enforce anything (deny a call, stop a run)? | No. It **records** policy/budget decisions; enforcement is feat-020. The audit trail is the witness, not the judge (§9). |
| Clock skew / ordering across concurrent runs | `seq` is assigned by the single-writer worker, so chain order is well-defined regardless of wall-clock; `timestamp_unix_nanos` is descriptive, `seq` is authoritative for the chain. |

## 9. Out of scope

- **A SIEM product or a log-search UI.** The SDK *emits* a tamper-evident audit
  projection and ships a query/export surface for offline reports and auditor bundles;
  it does not host a searchable audit console (requirements §11 — emit, don't build
  dashboards). The `siem` sink *exports to* a SIEM/syslog collector; it is not one.
- **Real-time alerting on audit events.** No "page on a `policy.decision` deny." That's
  a feat-007 listener (a Slack-on-failure-style reaction) or the team's existing
  alerting stack over the exported log — not an audit-trail responsibility (parity with
  requirements §11 / feat-009 §9).
- **Cryptographic signing / notarization / external anchoring in v1.** v1 is
  **hash-chain integrity only** (tamper-*evident*). Signing the head hash with a key,
  publishing it to a notary/transparency log, or timestamp-anchoring is a deliberate
  **later phase** — `head_hash()` is exposed now as the hook for it, but v1 does not
  sign or anchor.
- **Replacing the trace exporters.** The audit log is a *parallel projection*, not a
  substitute for OTLP/Langfuse/etc. The exporters remain the observability path; the
  audit sink is the governance path. They share one emission and one redaction, and
  diverge only on sampling and integrity.
- **Enforcement of any kind.** The audit trail does not deny a call, stop a run, or
  block spend. It **records** what happened, including the policy/budget decisions
  feat-020 made. Enforcement is feat-020 (the policy/governance feature); this feature
  is its witness.
- **Mutable / corrective edits to the log** (redacting a row after the fact, GDPR
  "right to erasure" on an immutable chain). Append-only is the point; lawful erasure
  on a hash chain (tombstoning + re-anchoring) is a known later-phase question, not v1.
- **Durable delivery guarantees beyond the sink's medium.** The jsonl/sqlite sinks are
  as durable as their file/db; cross-host replication, WORM storage, and retention
  policy are the deploying team's infrastructure, not the SDK's.

## 10. References

- [`../requirements.md`](../requirements.md) — FR-5 (metadata), FR-7 (errors), FR-8 (events), FR-9 (cost), FR-10 (interception), NFR-1/2/3 (non-block/fault/perf), NFR-6 (footprint), §5 (governance persona), §1.1 (governance thread)
- [`../design/design-principles.md`](../design/design-principles.md) — P1 (vendor-neutral; audit is its own package), P2 (opt-in install), P4 (OTel-first; the `otel` sink emits log records, custom fields `forgesight.*`), P5 (locked four SPIs unchanged), P6 (non-blocking, never-raises), P7 (redaction before write), P8 (named hash/algorithm config), P10 (sink conformance suite)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (custom data as `forgesight.*`-namespaced attributes; `error.type` stable; cost = `forgesight.usage.cost_usd`)
- [`../design/architecture.md`](../design/architecture.md) §3 (`Record`, metadata), §4 (SPIs), §7 (lifecycle), §8 (failure modes), §9 (sampling/perf)
- feat-002 (runtime — the single emission path), feat-003 (async pipeline — the tap rides it; head sampling), feat-007 (event bus — the audit-listener use case it generalises), feat-008 (interceptors — redaction before write), feat-006 (cost stamp), feat-009 (errors)
- feat-020 (policy/budget — *recorded* as `policy.decision`/`budget.event`, enforced there not here), feat-022 (ownership — `owner`/`team` attribution)
- Roadmap: features [`README.md`](./README.md) — Phase 5 (governance & compliance)
