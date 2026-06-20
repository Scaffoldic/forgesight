# feat-024: Agent identity & principal attribution

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-024 |
| **Title** | Agent identity & principal attribution — a stable, portable principal stamped on every run/span/metric/event |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-20 |
| **Target version** | 0.5 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-identity` |
| **Depends on** | feat-002 (telemetry runtime), feat-010 (config/bootstrap) |
| **Blocks** | none |

---

## 1. Why this feature

The SDK now stamps *ownership* onto every run (feat-022): `team`, `owner`, `repo`,
`sla_tier`, looked up from a declared registry. That answers "**who pays for** this
run?" — a FinOps question keyed on a declared table. It does **not** answer the
adjacent question every audit and incident asks: "**who or what executed** this run,
and is that subject the same one cost and audit can both key on?" Ownership is a
property attached *to* an agent; identity is the agent itself — the verifiable subject
of an action.

Today that subject is implicit and scattered. A run carries `agent.name` /
`agent.version` (feat-002), the registry may stamp a `team`, the deploy may set an
`environment` — but there is no single, stable, portable value that says *this run was
executed by principal X*, the same X across every span, every metric series, and (where
present) every audit event. The pains that follow:

- **"Which agent did this?" is archaeology.** A suspicious LLM call shows up in the
  backend. To name the actor you join `agent.name` from the span, the `team` from a
  registry stamp, the `environment` from a deploy label, and the on-behalf-of user from
  a log line three systems over — each present on some signals and missing on others.
  There is no one attribute that *is* the subject.
- **Cost and audit key on different things.** Chargeback rolls up on `team` (feat-022);
  an audit trail keys on `agent.name`; an incident review keys on whatever the on-call
  could reconstruct. Three keys for one actor means the three views never line up — you
  cannot say "everything principal X did this week" in one query.
- **Identity already exists in the environment, unused.** The agent runs under an OIDC
  token, a SPIFFE SVID, or a cloud-IAM role — a verified subject is *right there* in the
  process. Nothing maps it onto the telemetry, so the telemetry re-derives a weaker
  identity from a name string while the strong one goes unrecorded.
- **There is no OTel attribute for agent identity.** The GenAI conventions name
  `gen_ai.agent.name` / `gen_ai.agent.id` (and `id` is discouraged for transient
  instances — [otel-semconv §4.3](../design/otel-semantic-conventions.md#43-attribute-mapping)).
  None of them is a stable *principal*: a kind, an owner, an on-behalf-of, an
  environment, bound into one identifier. The spec hasn't shipped one, so the value
  isn't recorded at all.

This feature defines that subject: a stable `AgentPrincipal` — a URN such as
`forgesight:agent:acme/invoice-parser@2.3.0`, plus `kind`, `owner`, and attributes
(`environment`, `region`, `on_behalf_of`) — **resolved once at run start** and stamped
onto every signal under the neutral `forgesight.principal.*` namespace. It generalises
feat-022's ownership stamp into a first-class identity, so cost, audit, and incident
review all key on the *same* `forgesight.principal.id`.

## 2. Why this belongs in the SDK

- **The SDK is the only point that sees identity *and* every signal at run start.**
  Resolving the principal means reading what's already in the environment (a config
  principal, or claims from a token already present) and merging it into
  `TelemetryContext` at run start, so it lands on the root span and every child (FR-5
  propagation). Only the runtime is in that position. A side-car that joins identity
  *after* export has the same disease feat-022 named for ownership: late, dirty,
  per-backend data. Stamping at source is clean and universal.
- **One subject is the entire value.** The reason "which agent did this" is archaeology
  is that the actor is spread across name + registry stamp + deploy label + log line.
  A *single* `forgesight.principal.id`, identical on every span / metric / event of a
  run, collapses that join to a lookup. That consistency is exactly what makes audit
  attribution (feat-023) and cost attribution (feat-022) coherent: they stop keying on
  three different proxies and key on one principal.
- **It generalises a stamp the SDK already does.** feat-022 resolves `(name, version)`
  → ownership and merges it into `TelemetryContext.metadata` at run start. Identity is
  the same mechanism with a wider input (the environment, not just a declared table) and
  a reserved output namespace (`forgesight.principal.*`, not arbitrary business
  metadata). The runtime hook, the context-merge, the caller-wins rule — all reused. The
  registry's ownership *feeds* the principal's `owner`; the two compose, they don't
  collide.
- **OTel has no attribute for this, so the SDK must own it cleanly.** Per P4 we layer on
  OTel and namespace what OTel doesn't define under `forgesight.*` (exactly as cost is
  `forgesight.usage.cost_usd`, ADR-0005). There is no `gen_ai.*` principal; we define
  `forgesight.principal.*` and never squat on a `gen_ai.*` identifier the spec hasn't
  shipped.
- **The anti-pattern if we don't:** every agent re-derives a weak identity from a name
  string, the strong verified subject in the environment goes unrecorded, and cost,
  audit, and incident review each invent their own actor key — so "everything principal
  X did" is never a single query. The SDK has the run-start hook and the context; not
  stamping the subject wastes both.

This realises the **FinOps / governance persona** (requirements §5) on its *attribution*
axis, builds on **FR-5** (business metadata as the propagation mechanism), **FR-1**
(agent identity is part of run tracking), and **NFR-1** (the resolution stays off the
hot path). It is a Phase 5 platform feature alongside the registry (feat-022) and audit
(feat-023).

## 3. How consuming agents/teams benefit

**Before.** Naming the actor behind a signal is a reconstruction. The on-call reads
`agent.name=invoice-parser` off a trace, finds the `team` on a registry stamp if it's
present, guesses the `environment` from a deploy label, and pings Slack to learn which
user the run acted on behalf of. Cost rolls up on `team`, the audit log keys on
`agent.name`, the incident doc keys on whatever was reconstructed — three keys, one
actor, and the views never reconcile. The OIDC/SPIFFE/IAM identity the process actually
ran under is sitting in the environment, recorded nowhere.

**After.**

- **Day 0 — declare the principal once; every signal carries it.** Add an `identity:`
  block (a static principal, or `driver: oidc` to read the claim already present). The
  SDK resolves the `AgentPrincipal` at run start and stamps `forgesight.principal.id`
  (the URN), `forgesight.principal.kind`, `forgesight.principal.owner`, and the
  attribute keys onto the root span and every child (FR-5). Agent authors write *zero*
  identity-tagging code, and the principal is identical by construction across signals.
- **Day 1 — "everything principal X did" is one query.** Because the *same*
  `forgesight.principal.id` is on every span, every metric series, and every audit event
  of a run, "show me everything `forgesight:agent:acme/invoice-parser@2.3.0` did this
  week" is a single filter — not a four-system join. The actor is a value, not a
  reconstruction.
- **Day 3 — cost and audit reconcile because they key on the same subject.** Chargeback
  (feat-022) and the audit trail (feat-023) both group by `forgesight.principal.id`. The
  cost view and the audit view line up row-for-row because they name the actor the same
  way. The registry's `owner` flows into `forgesight.principal.owner`, so ownership and
  identity are one coherent stamp, not two competing ones.
- **Incident — the actor in one hop.** The on-call reads the principal URN, its `kind`,
  its `owner`, its `environment`, and its `on_behalf_of` straight off the trace, because
  the resolver stamped them at run start. No deploy-label guessing, no Slack archaeology.
- **The win:** the actor stops being archaeology. The verified identity already in the
  environment — or a declared one for agents that have none — becomes a single, stable,
  neutral `forgesight.principal.id` on every signal, so cost, audit, and incident review
  all name the same subject. ForgeSight does this by *consuming and stamping* identity;
  it never mints, issues, verifies, or rotates a credential — that stays with the app's
  auth/IdP (see §9).

## 4. Feature specifications

### 4.1 User-facing experience

```yaml
# forgesight.yaml — the static principal (default driver): identity from config
identity:
  enabled: true
  driver: "static"
  principal:
    org: "acme"                  # → URN forgesight:agent:acme/<name>@<version>
    kind: "agent"                # agent | tool | service
    owner: "fin-platform@acme.com"
    attributes:
      environment: "prod"
      region: "eu-west-1"
```

```yaml
# forgesight.yaml — consume identity already present in the environment
identity:
  enabled: true
  driver: "oidc"                 # read claims from a token already in the process;
  claims:                        # never fetch, never mint (P6, §9)
    org: "azp"                   # which claim maps to which principal field
    owner: "email"
    on_behalf_of: "act.sub"      # delegation / on-behalf-of claim, when present
```

```python
# python — wire identity once at bootstrap; every signal is stamped automatically
import forgesight
from forgesight_identity import Identity

forgesight.configure(
    identity=Identity.from_config(),               # or Identity.static(org="acme", owner=…)
)

# The agent author writes nothing extra — the principal is resolved at run start:
from forgesight import telemetry

with telemetry.agent_run("invoice-parser", version="2.3.0") as run:
    ...   # every span/metric/event now carries
          # forgesight.principal.id   = "forgesight:agent:acme/invoice-parser@2.3.0"
          # forgesight.principal.kind = "agent"
          # forgesight.principal.owner = "fin-platform@acme.com"
          # forgesight.principal.environment = "prod", …region, …on_behalf_of
```

```python
# python — read the resolved principal (e.g. to log it, or pass it to an audit sink)
principal = run.principal()                        # the AgentPrincipal for this run
print(principal.id)        # forgesight:agent:acme/invoice-parser@2.3.0
print(principal.kind, principal.owner, principal.attributes["environment"])
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
import { Identity } from '@agentforge/sdk-identity';

configure({ identity: Identity.fromConfig() });   // driver: static | env | oidc | spiffe
```

The resolver runs **once at run start** (cached for the process where the principal is
static; re-read per run only when the driver's claims can change between runs). A
caller-set `forgesight.principal.*` key always wins over a resolved one, so a run can
override (e.g. a one-off `environment=staging`) without editing config — the same
`caller_wins` rule feat-022 uses for ownership.

### 4.2 Public API / contract

```python
# forgesight_identity/model.py — experimental
from enum import Enum

class PrincipalKind(str, Enum):
    AGENT = "agent"; TOOL = "tool"; SERVICE = "service"

@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    """The verifiable subject of a run. Resolved once at run start; stamped on every signal."""
    id: str                                   # URN: forgesight:agent:<org>/<name>@<version>
    kind: PrincipalKind = PrincipalKind.AGENT
    owner: str | None = None                  # composes with feat-022 ownership
    attributes: Mapping[str, str] = field(default_factory=dict)   # environment, region, on_behalf_of, …

    @classmethod
    def urn(cls, org: str, name: str, version: str | None) -> str: ...   # build the URN form
    def principal_attributes(self) -> dict[str, str]: ...     # → forgesight.principal.* keys
```

```python
# forgesight_identity/resolver.py — experimental (package-local Protocol, NOT a locked SPI)
@runtime_checkable
class IdentityResolver(Protocol):
    """Resolve the principal for a run from the environment. Reads what's already present —
    a config principal, or claims from a token already in the process. Never fetches, never
    mints (P6, §9). `static` shipped as default; `env`/`oidc`/`spiffe` shipped as drivers."""
    def resolve(self, *, name: str, version: str | None) -> AgentPrincipal | None: ...
```

```python
# forgesight_identity/identity.py — experimental
class Identity:
    """Bootstrap handle. Wires the chosen IdentityResolver as the run-start principal provider."""
    @classmethod
    def from_config(cls) -> "Identity": ...                 # reads the identity.* config block
    @classmethod
    def static(cls, *, org: str, kind: str = "agent",
               owner: str | None = None, **attributes: str) -> "Identity": ...
    @classmethod
    def from_resolver(cls, resolver: "IdentityResolver") -> "Identity": ...   # pluggable

    def principal_for(self, name: str, version: str | None) -> AgentPrincipal | None: ...
```

```python
# forgesight_core/runtime/scope.py — additive, experimental (one accessor on the existing scope)
class RunScope:
    def principal(self) -> "AgentPrincipal | None": ...     # the principal resolved for this run
```

The principal is stamped via the **locked** `TelemetryContext` metadata mechanism
(feat-002) under the reserved `forgesight.principal.*` keys — **no new SPI on the
telemetry path**. `IdentityResolver` is a new *package-local* Protocol (not part of the
locked `-api` four-SPI surface; it's an identity-loading detail, exactly as feat-022's
`RegistrySource` is). It ships with its own conformance suite (P5/P10 — every resolver
driver is verified against the contract, feat-011). All symbols are **experimental**
within 0.x.

### 4.3 Internal mechanics

**Resolution at run start.** Identity installs a run-start hook over feat-002's existing
context machinery (*not* a new SPI) — the same seam feat-022 stamps ownership through:

```
telemetry.agent_run(name, version)  enters
   │  bind TelemetryContext, generate run_id (feat-002)
   │
   ├── principal = identity.principal_for(name, version)     # resolver: in-memory, no I/O
   │      static  → build URN from config org + (name, version), attach config attributes
   │      oidc    → read claims ALREADY in the process; map per `claims:`; never fetch
   │      → AgentPrincipal(id=urn, kind, owner, attributes)
   │
   │      if principal:
   │         ctx.metadata["forgesight.principal.id"]    = principal.id
   │         ctx.metadata["forgesight.principal.kind"]  = principal.kind
   │         ctx.metadata["forgesight.principal.owner"] = principal.owner
   │         for k, v in principal.attributes:                       # environment, region, …
   │            ctx.metadata[f"forgesight.principal.{k}"] = v        # caller-set key wins
   │
   └── open root span carrying the merged metadata → propagates to every child (FR-5)
```

Because the principal lands in `TelemetryContext.metadata` at run start, it rides the
*same* FR-5 propagation as any business metadata: it is on the root span and copied into
every child (`TelemetryContext.child()` copies the metadata dict, feat-002 §4.3), so a
fan-out under `asyncio.gather` carries an identical principal on every leaf. It is on
every metric series the run emits (the run's metadata is the metric attribute set,
feat-005) and on every audit event (feat-023 reads the same context) — one subject,
every signal.

**Composition with feat-022 ownership.** Identity and the registry stamp through the
same run-start seam and compose by namespace, not collision:

- The registry stamps **business-metadata** keys (`team`, `owner`, `repo`, `sla_tier`)
  — feat-022's job, unchanged.
- Identity stamps **reserved** `forgesight.principal.*` keys — this feature's job.
- The principal's `owner` is *seeded from* the registry's `owner` when identity has none
  declared (identity reads the resolved registry entry if one is wired), so the two
  always agree on the owner rather than disagreeing. Where both are configured, the
  explicit `identity:` value wins for `forgesight.principal.owner`; the registry's
  `owner` still stamps as business metadata. They are two coherent views of one actor,
  not two competing owners.

`forgesight.principal.id` is therefore the *join key* the registry's chargeback
(feat-022) and the audit trail (feat-023) both group by — the consistent subject §1
promised.

**Drivers (all consume, none fetch — P6).** Resolution is an in-memory step:

| Driver | Reads | Never does |
|---|---|---|
| `static` (default) | the `identity.principal` config block | — |
| `env` | principal fields from named environment variables | network |
| `oidc` | claims from an OIDC token **already in the process** (decode, no verify) | fetch a token; verify a signature; call an IdP |
| `spiffe` | the SVID / trust-domain id **already present** in the workload | call the SPIFFE workload API on the hot path |

Every driver maps its input onto the neutral `AgentPrincipal` and returns — *no network
on the hot path, ever* (P6, NFR-1). The principal is **cached for the process** when it
is static for the lifetime of the process; for a driver whose claims can differ
per-run (an on-behalf-of that changes per request), the resolver re-reads the
already-present claim at run start — still an in-memory decode, never a fetch.

**Sensitivity (P7).** A principal can carry sensitive values — an `owner` email, an
`on_behalf_of` subject. The `forgesight.principal.*` keys are ordinary metadata on the
record, so they pass through the **redaction interceptor (feat-008)** before export
exactly like any other metadata: a team that treats `on_behalf_of` as PII redacts it
with the same `redact_keys` mechanism. Identity stamps; the content-capture and
redaction gates still apply.

### 4.4 Module packaging

- **`forgesight-identity`** is a new opt-in integration package (P2). It holds the
  `AgentPrincipal` / `PrincipalKind` model, the `IdentityResolver` Protocol + the
  `static`/`env`/`oidc`/`spiffe` drivers, the `Identity` bootstrap handle, and the
  run-start stamping hook. It depends only on `-api` and `-core` — **no vendor SDK, no
  IdP client** (P1). The `oidc`/`spiffe` drivers *decode* claims that are already present
  (stdlib only); they never pull an IdP SDK.

```bash
pip install forgesight-identity
```

```yaml
# forgesight.yaml
identity:
  enabled: true
  driver: "static"
  principal:
    org: "acme"
    owner: "fin-platform@acme.com"
```

**Entry-point registration** under the SDK's module-load group so feat-010's bootstrap
wires the run-start hook automatically, plus a group for resolver drivers (mirroring
feat-022's `forgesight.registry_sources`):

```toml
# forgesight-identity/pyproject.toml
[project.entry-points."forgesight.modules"]
identity = "forgesight_identity:install"

# Identity resolver drivers resolvable by name from config:
[project.entry-points."forgesight.identity_resolvers"]
static = "forgesight_identity.resolver:StaticResolver"
env    = "forgesight_identity.resolver:EnvResolver"
oidc   = "forgesight_identity.resolver:OidcResolver"
spiffe = "forgesight_identity.resolver:SpiffeResolver"
```

No telemetry-path entry point (exporter/interceptor) is added — identity stamps metadata
through the existing context, exactly as the registry does.

### 4.5 Configuration

```yaml
identity:
  enabled: true               # master switch (default: false — install does nothing until on)
  driver: "static"            # "static" | "env" | "oidc" | "spiffe" | "<custom-registered-name>"

  # --- static driver (default): the principal is declared in config ---
  principal:
    org: "acme"               # required (static): the URN org segment
    kind: "agent"             # agent | tool | service (default: agent)
    owner: "fin-platform@acme.com"   # optional; seeds forgesight.principal.owner
    attributes:               # arbitrary principal attributes → forgesight.principal.<k>
      environment: "prod"
      region: "eu-west-1"

  # --- env / oidc / spiffe drivers: map already-present input onto principal fields ---
  claims:                     # which env var / token claim feeds which principal field
    org: "azp"
    owner: "email"
    on_behalf_of: "act.sub"

  prefix: "forgesight.principal."   # the reserved stamp namespace (default; do not change lightly)
  caller_wins: true           # a caller-set forgesight.principal.* key overrides the resolved one
  on_unresolved: "warn"       # "warn" | "ignore" | "error" — behaviour when no principal resolves
```

**Validation rules.** `driver: static` requires `principal.org`; `driver: oidc`/`spiffe`
require a `claims` map naming at least `org`. `kind` ∈ `{agent, tool, service}`.
`on_unresolved` ∈ `{warn, ignore, error}` — `error` is for strict shops that require
*every* run to carry a principal (fail-fast at run start, mirroring feat-022's
`on_unmatched=error`); `warn` (default) stamps nothing and counts the miss in
`sdk_identity_unresolved_total`; `ignore` is silent. `claims` keys must name valid
principal fields (`org`/`owner`/`on_behalf_of`/…). No driver may perform network I/O —
enforced by the resolver conformance suite (P10). Unknown keys rejected at `configure()`
(architecture §8).

**Defaults.** `identity.enabled` defaults `false`; installing the package stamps nothing
until enabled (P2). `driver` `static`; `kind` `agent`; `prefix`
`forgesight.principal.`; `caller_wins` true; `on_unresolved` warn.

**Env overrides** (feat-010): `FORGESIGHT_IDENTITY_ENABLED`,
`FORGESIGHT_IDENTITY_DRIVER`, `FORGESIGHT_IDENTITY_ORG`, `FORGESIGHT_IDENTITY_OWNER`,
`FORGESIGHT_IDENTITY_KIND`, … kwargs > env > YAML.

## 5. Plug-and-play & upgrade story

Add it later with `pip install forgesight-identity` + the `identity:` YAML. No
agent-code change: resolution is a bootstrap-installed run-start hook over existing
context, so every run starts carrying a principal the moment identity is wired — exactly
the feat-022 add-later story. Remove it with `pip uninstall` + dropping the config; runs
keep emitting cost/ownership/structure unchanged, just without the auto-stamped
`forgesight.principal.*`.

Upgrade safety: the feature rides the **locked** `TelemetryContext` metadata mechanism
(feat-002) and the reserved `forgesight.principal.*` namespace (a `forgesight.*`
extension under P4, like cost). New optional `AgentPrincipal` attributes or shipped
drivers land in minor bumps behind defaults (P5). `IdentityResolver` is a package-local
Protocol; adding a shipped driver (e.g. a new token format) is additive. The
model/drivers are experimental within 0.x — signature changes are changelog-called-out,
the contracts beneath them do not move.

## 6. Cross-language parity

Identical across Python / TypeScript: the `AgentPrincipal` shape, the URN form
(`forgesight:agent:<org>/<name>@<version>`), the `forgesight.principal.*` attribute
namespace, the driver set (`static`/`env`/`oidc`/`spiffe`), the run-start resolution
point, the `caller_wins` rule, the consume-only boundary (read claims already present;
never fetch/mint), and the `on_unresolved` behaviour. Allowed to differ: idiomatic
naming (`fromConfig` vs `from_config`), the claim-decoding helper, and the env/token
access mechanics. Python lands first (0.5); TypeScript follows the same surface.

## 7. Test strategy

- **Unit:** URN construction from `(org, name, version)`; `principal_attributes()` →
  `forgesight.principal.*` key mapping; `static` driver builds the principal from config;
  `env`/`oidc` drivers map named claims onto principal fields; `caller_wins` precedence
  (a caller-set `forgesight.principal.environment` survives resolution); `on_unresolved`
  variants (warn counts, ignore silent, error raises at run start).
- **Integration:** a run with identity wired carries the *same* `forgesight.principal.id`
  on the root span **and** a child LLM span (FR-5 propagation, snapshot vs the in-memory
  exporter, feat-011); a fan-out under `asyncio.gather` carries an identical principal on
  every leaf; the principal is present on a metric series and (with feat-023 wired) on an
  audit event.
- **Composition:** with feat-022 also wired, `forgesight.principal.owner` agrees with the
  registry's `owner`; chargeback grouped by `forgesight.principal.id` matches the audit
  trail grouped by the same key (the join-key invariant).
- **No-hot-path-I/O (the load-bearing test, P6):** every shipped driver resolves with the
  network blocked — a resolver that attempts a fetch fails the conformance suite; the
  `oidc`/`spiffe` drivers decode an already-present claim and never open a socket.
- **Sensitivity (P7):** `forgesight.principal.owner` / `on_behalf_of` pass through the
  redaction interceptor (feat-008) and are redacted when named in `redact_keys`.
- **Conformance:** the `IdentityResolver` conformance suite (P10) every driver must pass
  — resolves in-memory, never performs I/O, returns a well-formed `AgentPrincipal` or
  `None`.
- **Example:** a one-agent workspace with a `static` principal and an `oidc` principal,
  showing the same `forgesight.principal.id` across span/metric/event — the headline demo.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Tempting to *verify* the OIDC/SPIFFE token in the resolver | Explicitly out of scope (§9): drivers **decode** an already-present, already-trusted claim. Verification is the app's auth/IdP. The conformance suite forbids network I/O, which forbids verification-by-callout. |
| `forgesight.principal.*` collides with a future `gen_ai.*` agent-identity attribute | We namespace under `forgesight.*` (P4) precisely so an upstream `gen_ai.*` principal, when it ships, is mapped *to* by feat-004 without a clash — same posture as cost (ADR-0005). |
| A driver's claim is missing at run start | `on_unresolved` warn/ignore/error decides; `warn` stamps nothing and counts the miss (so operators find runs with no resolvable principal — the gap surfaces, it doesn't vanish). |
| Sensitive principal fields (owner email, on-behalf-of) in traces | `forgesight.principal.*` are ordinary metadata; subject to the same redaction interceptor (feat-008) as any metadata (P7). |
| Identity `owner` vs registry `owner` disagree | Identity seeds from the registry's `owner` when undeclared; where both are explicit, `identity:` wins for `forgesight.principal.owner` and the registry's still stamps as business metadata — two coherent views, documented precedence. |
| Per-run claim re-read adds hot-path cost | Static principal is cached for the process; only drivers whose claims vary per-run re-read, and that re-read is an in-memory decode (no fetch) — within the NFR-1 budget. |

## 9. Out of scope

- **Being an identity provider / token issuer / credential mint or rotation.** ForgeSight
  **consumes and stamps** identity; it does not issue, sign, mint, refresh, or rotate any
  credential. Credential lifecycle belongs entirely to the consuming app's auth/IdP. This
  is the hard scope boundary of the feature.
- **Authentication or authorization enforcement.** Identity stamps a subject onto
  telemetry; it does not authenticate a caller or decide allow/deny. Policy enforcement
  (budgets, kill-switch, allow/deny on the principal) is feat-020 / feat-025 — which key
  their decisions on the *same* `forgesight.principal.id` this feature stamps. This
  feature is *attribution*, not control.
- **Verifying tokens / validating signatures / checking trust chains.** Drivers decode an
  already-present, already-trusted claim. They do not verify an OIDC signature, validate a
  SPIFFE trust domain, or check a JWT against a JWKS endpoint — that is the app's auth
  layer, and it would require the hot-path I/O P6 forbids.
- **Calling out to a remote IdP / token endpoint / workload API on the hot path.** No
  driver performs network I/O; all read what is already in the process (P6). Fetching or
  refreshing a token is the app's job, done before the SDK ever sees it.
- **A secrets manager.** Identity references values from config/env/claims; it does not
  store, fetch, or manage secrets (config `${ENV}` interpolation, feat-010, is the
  sanctioned way to reference one).
- **Being the canonical identity registry.** It reads a declared/ambient principal and
  stamps it; it is not the system of record for who an agent *is* — that's the app's IdP
  and, for ownership, the registry (feat-022).

## 10. References

- [`../requirements.md`](../requirements.md) — FR-5 (business metadata — the stamp
  mechanism), FR-1 (agent identity in run tracking), §5 (FinOps/governance persona), NFR-1 (hot-path budget)
- [`../design/architecture.md`](../design/architecture.md) §3 (`TelemetryContext`, business metadata), §7 (lifecycle / run-start hook), §8 (failure modes)
- [`../design/design-principles.md`](../design/design-principles.md) — P1 (vendor-neutral), P2 (plug-and-play), P4 (layer on OTel / namespace under `forgesight.*`), P6 (non-blocking — no hot-path I/O), P7 (sensitive metadata → redaction), P5/P10 (package-local Protocol + conformance)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3 (custom data as `forgesight.*`-namespaced attributes; `gen_ai.agent.id` discouraged for transient ids)
- feat-002 (runtime / `TelemetryContext` / FR-5 propagation / run-start hook), feat-010 (config/bootstrap + entry-point groups)
- feat-022 (registry/ownership — identity generalises the same stamp; `owner` composes), feat-023 (audit — keys on `forgesight.principal.id`), feat-020/feat-025 (policy — enforce on the same principal)
- Roadmap: features [`README.md`](./README.md) — Phase 5 (identity, audit, platform attribution)
