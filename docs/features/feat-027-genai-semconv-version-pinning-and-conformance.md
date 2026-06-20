# feat-027: GenAI semantic-convention version pinning & conformance

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-027 |
| **Title** | GenAI semantic-convention version pinning & conformance — a declared semconv pin, a CI-asserted conformance snapshot, and a config-gated dual-emit migration window |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-20 |
| **Target version** | 0.5 (and ongoing as the spec stabilises) |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-otel` (extends feat-004); conformance test in `forgesight-testing` (feat-011) |
| **Depends on** | feat-004 (OTel exporter & semconv mapping), feat-011 (testing & conformance harness) |
| **Blocks** | none |

---

## 1. Why this feature

feat-004 made one promise the whole SDK leans on: ForgeSight is the *single* place the
domain model maps onto OTel's GenAI semantic conventions, so callers depend on our
domain model and never touch raw `gen_ai.*` names. ADR-0004 backs that promise with
"pin + isolate + version" — the mapping is pinned to a commit (`SEMCONV_COMMIT`) and
stamped with a version (`SEMCONV_VERSION`). But the GenAI conventions are still
**entirely at `Development` stability with no tagged release** and are actively renaming
identifiers (`gen_ai.system` → `gen_ai.provider.name` already happened). So the
attribute *names* ForgeSight emits are a moving target, and two of the three pillars in
ADR-0004 are today only *promised*, not *enforced*: there is a `SEMCONV_VERSION` string
but it is not surfaced to consumers as a contract, and there is no machine check that
the names we emit actually match the revision we claim.

This bites at the seam between ForgeSight and everything downstream of it.

Concrete scenarios that hit teams today:

- **The silent dashboard break.** A platform team builds a Grafana dashboard keyed on
  `gen_ai.usage.input_tokens`. The next ForgeSight release re-pins to a newer semconv
  commit in which that attribute was renamed. The dashboard's panels go blank — no
  error, no warning, just empty graphs noticed days later when someone asks "why is our
  token cost zero?" The rename was invisible because nothing made it visible: the
  emitted version on the wire didn't change in a way the backend could key on, and there
  was no overlap window where both names were present.
- **"Which convention is this stream?"** A backend ingests spans from three ForgeSight
  versions across a fleet mid-rollout. An operator wants to know which attribute set to
  expect for a given span and has no answer — `SEMCONV_VERSION` exists in the code but
  isn't a queryable, documented dimension on the exported resource that a backend can
  group or alert on.
- **The re-pin that nobody can review safely.** A contributor bumps `SEMCONV_COMMIT` to
  track an upstream rename. Did the emitted attribute set actually change? Which span
  kinds? Today the answer is "read the diff of `semconv.py` very carefully and hope,"
  because there is no captured baseline of what ForgeSight emits per span kind to diff
  against. Drift slips through code review.
- **Flag-day migrations.** When ForgeSight *does* follow an upstream rename, every
  backend must cut over on the same release or break. There is no deprecation window in
  which both the old and new attribute names are present so backends can migrate at their
  own pace. The `emit_legacy_system` flag already proves the pattern works for one
  attribute (`gen_ai.system`) — but it's a one-off, not a general migration mechanism.

The scenario this feature is built to defeat, end to end: **a consumer pins ForgeSight;
a backend dashboard is built on the attribute names ForgeSight emits; the upstream spec
renames an attribute.** Without this feature, the dashboard silently breaks on the next
SDK bump. With it, the declared pin on the resource makes the active convention
queryable, the conformance snapshot makes the rename *visible in CI* before it ships,
and the dual-emit window makes the rename *non-breaking* for backends mid-migration.

This is the last mile of ADR-0004: turning "pin + isolate + version" from a promise in a
module docstring into a declared contract, a CI-enforced check, and a controlled
migration path — so the one mapping seam protects every backend and every consumer at
once (P4).

## 2. Why this belongs in the SDK

- **ForgeSight is the single mapping seam — pinning here protects everyone at once
  (P4).** The whole reason `SemConvMapper` exists is that the convention is volatile and
  must be owned in *one* place so callers never touch raw `gen_ai.*` names. The version
  pin, the conformance snapshot, and the migration window are properties *of that seam*.
  Push them out to agents and you've re-created exactly the fragmentation feat-004 ended:
  N agents each guessing which convention revision they emit, N dashboards each breaking
  on a different release. The seam is already the choke point; this feature makes the
  choke point declare, verify, and migrate.
- **Only the SDK can make the pin a contract, because only the SDK owns the wire
  format.** A consumer can't pin a convention version ForgeSight doesn't surface, and a
  backend can't key on a version ForgeSight doesn't stamp queryably. Surfacing the pin in
  config and on the exported resource is something only the producer of the spans can do.
  ADR-0004 names auditability — "a backend must be able to tell which revision produced a
  span" — as a decision driver; this feature delivers that driver.
- **Conformance-over-trust applies to ForgeSight's own mapping, not just third-party
  SPIs (P10).** feat-011 ships conformance suites that keep N *exporters* honest to one
  contract. The semconv mapping is itself a contract — "for an `invoke_agent` span,
  ForgeSight emits exactly this attribute set at this pinned revision" — and today
  nothing asserts it holds across a re-pin. A snapshot asserted in CI is the same P10
  mechanism turned inward: it keeps *ForgeSight's mapping* honest to *its declared pin*,
  so a re-pin that silently changes the emitted set fails the build (NFR-7's quality bar
  extended to the mapping surface).
- **The stability contract is a P5 obligation the SDK must state, not each agent.**
  P5 locks the domain model and SPIs; but the *mapping* of that model onto `gen_ai.*`
  names rides an experimental upstream spec. Someone has to draw the line between the
  parts ForgeSight treats as stable (span kinds, operation names, the cost extension)
  and the parts that track-experimental upstream (attributes the spec may yet remove).
  That line is a property of the seam ForgeSight owns; an agent can't draw it because it
  doesn't own the mapping.
- **The anti-pattern if we don't:** ADR-0004's three pillars stay half-built. The pin is
  a code constant nobody downstream can see; re-pins ship unreviewed drift; every upstream
  rename is a flag-day break for every backend. The SDK has the seam and the conformance
  harness already in hand; not closing this loop wastes both and leaves the P4 promise
  unenforced.

This rides **locked surfaces** — it adds no new SPI. The pin is surfaced through the
existing `forgesight-otel` exporter config and the existing Resource-attribute path
(feat-004); the dual-emit knob mirrors the existing `emit_legacy_system` flag; the
conformance check is a new case in the existing feat-011 harness. It is *config + a
snapshot + a flag*, not a new contract.

## 3. How consuming agents/teams benefit

**Before.** The attribute names a backend keys on are an undeclared, moving target. A
ForgeSight upgrade can rename an attribute out from under a dashboard with no warning and
no overlap window; an operator staring at three fleet versions can't tell which
convention each emits; a contributor re-pinning the spec can't prove what changed; a
rename is a flag-day every backend must survive together.

**After.**

- **Day 0 — the active convention is declared and on the wire.** ForgeSight surfaces the
  pin as config (`semconv.version` / `semconv.commit`, defaulting to the built-in
  `SEMCONV_VERSION` / `SEMCONV_COMMIT`) and stamps it on every span's Resource as
  `forgesight.semconv_version`. A backend can *group by* and *alert on* the convention
  revision; a consumer can read exactly which version their stream targets. "Which
  convention is this?" is now a queryable dimension, not tribal knowledge.
- **Day 7 — a re-pin is a reviewable diff, not a leap of faith.** The conformance
  snapshot is a captured, machine-checkable baseline of the attribute set ForgeSight
  emits for each span kind (`invoke_agent` / `chat` / `execute_tool` / MCP / …). A
  contributor who bumps `SEMCONV_COMMIT` and changes the emitted set must regenerate the
  snapshot; the diff shows *exactly* which attributes moved on which span kind, and CI
  fails any unintended drift. The rename is visible in the PR, before it ships.
- **Day 14 — a rename is a migration window, not a flag-day.** When ForgeSight follows an
  upstream rename, the mapper can **dual-emit** the previous and current attribute names
  for a deprecation window, gated by `semconv.compat` (the same shape as
  `emit_legacy_system`, generalised). A backend keyed on the old name keeps working while
  it migrates to the new one; both are present on the span during the window. No backend
  cuts over on the SDK's schedule.
- **Anytime — the stability contract sets expectations honestly.** ForgeSight publishes,
  per attribute group, which parts of the mapping it treats as **stable** (span kinds,
  operation names, the `forgesight.*` extensions like `forgesight.usage.cost_usd`) versus
  **tracking-experimental** (attributes the upstream spec may still rename or remove). A
  consumer reading the contract knows which names are safe to build a long-lived dashboard
  on and which to treat as provisional — no surprises, by declaration (P5).
- **The win:** the single mapping seam that already insulates callers from spec churn now
  *declares* what it emits, *proves* it in CI, and *migrates* without a flag-day. The
  consumer who pinned ForgeSight and built a dashboard on its attribute names is protected
  at the seam, once, for every backend — instead of discovering a rename when their graphs
  go blank.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — read the active semconv pin; nothing else changes about instrumentation
import forgesight
from forgesight_otel import OTelExporter, SEMCONV_VERSION, SEMCONV_COMMIT

# Default: the built-in pin is surfaced as config and stamped on the Resource.
forgesight.configure(exporters=[OTelExporter(endpoint="http://otel-collector:4317")])
print(SEMCONV_VERSION)   # e.g. "genai-dev-2026-06" — what every span's Resource carries

# Opt into the migration window when ForgeSight follows an upstream rename:
exporter = OTelExporter(
    endpoint="http://otel-collector:4317",
    semconv_compat="dual",        # "current" (default) | "dual" (current + previous names)
)
forgesight.configure(exporters=[exporter])
# During the deprecation window, a renamed attribute is emitted under BOTH names,
# so a backend keyed on the old name keeps working while it migrates.
```

```yaml
# forgesight.yaml — the pin and the migration mode live under the otel exporter config
exporters:
  - name: otel
    config:
      endpoint: "http://otel-collector:4317"
      semconv:
        # version/commit default to the built-in pin; pinning explicitly makes the
        # contract visible in config and lets ops assert "this deploy targets X".
        version: "genai-dev-2026-06"
        commit: "open-telemetry/semantic-conventions-genai@<sha>"
        compat: "current"          # "current" | "dual"
```

```typescript
// typescript (parity sketch — targets the same surface)
import { configure } from '@agentforge/sdk';
import { OTelExporter, SEMCONV_VERSION } from '@agentforge/sdk-otel';

configure({
  exporters: [new OTelExporter({
    endpoint: 'http://otel-collector:4317',
    semconvCompat: 'dual',         // 'current' | 'dual'
  })],
});
```

Nothing about the agent's instrumentation calls changes. The exporter is still the only
thing that knows about OTLP and the convention; this feature adds a *declared* pin on the
resource, a *config knob* for the migration window, and a CI check — all behind defaults
that reproduce today's behaviour exactly.

```python
# python — regenerate the conformance snapshot after an intentional re-pin (CI helper)
#   python -m forgesight_otel.conformance --update-snapshot
# Produces semconv_snapshot.json: the exact attribute key set ForgeSight emits per
# span kind at the current pin. CI asserts the live mapping matches this snapshot;
# an unreviewed change to the emitted set fails the build (P10).
```

### 4.2 Public API / contract

Real constants that exist today in `forgesight_otel/semconv.py` (feat-004) — **stable**;
the pin is stamped on every span's Resource as `forgesight.semconv_version`:

```python
# forgesight_otel/semconv.py — EXISTING (feat-004), stable
SEMCONV_COMMIT = "open-telemetry/semantic-conventions-genai@main"   # stable
SEMCONV_VERSION = "genai-dev-2026-06"                                # stable: on Resource
FORGESIGHT_SEMCONV_VERSION = "forgesight.semconv_version"            # stable: Resource key
```

Proposed additions — the version/compat config surfaced on the exporter, and the
conformance-snapshot format:

```python
# forgesight_otel/semconv.py — NEW (feat-027)
from enum import Enum

class SemConvCompat(str, Enum):
    """How the mapper handles attributes that moved across a re-pin."""
    CURRENT = "current"   # emit only the current (pinned) attribute names — default
    DUAL    = "dual"      # also emit the previous names for renamed attrs (deprecation window)

# The previous → current rename map for the active migration window. Empty when no
# rename is in flight; populated (with the prior name) for one minor after a re-pin
# that renames an attribute. This generalises the one-off emit_legacy_system flag.
SEMCONV_RENAMES: Mapping[str, str] = {
    # current_key: previous_key   (e.g. "gen_ai.provider.name": "gen_ai.system")
}

class SemConvMapper:                                  # experimental — internals may move
    def __init__(self, *, compat: SemConvCompat = SemConvCompat.CURRENT) -> None: ...
    # existing pure-mapping surface (feat-004), now compat-aware:
    def span_name(self, record: Record) -> str: ...
    def span_kind(self, record: Record) -> SpanKind: ...
    def attributes(self, record: Record, *, capture_content: bool = False,
                   emit_legacy_system: bool = False) -> dict[str, AttributeValue]: ...
    # when compat is DUAL, attributes() additionally mirrors each renamed key to its
    # previous name from SEMCONV_RENAMES, so both are present during the window.
```

```python
# forgesight_otel/conformance.py — NEW (feat-027), the snapshot contract
@dataclass(frozen=True, slots=True)
class SemConvSnapshot:
    """The machine-checkable baseline of what ForgeSight emits at a given pin.

    Maps each span kind / operation name → the exact set of attribute *keys* the
    mapper emits for a canonical record of that kind. Content-gated and opt-in keys
    are listed separately so the conformance assertion is deterministic regardless
    of capture_content / emit_legacy_system."""
    semconv_version: str                              # the pin this snapshot was taken at
    semconv_commit: str
    base_attrs: Mapping[str, frozenset[str]]          # op_name -> always-emitted keys
    content_attrs: Mapping[str, frozenset[str]]       # op_name -> capture_content-gated keys
    legacy_attrs: Mapping[str, frozenset[str]]        # op_name -> emit_legacy_system keys

    @classmethod
    def capture(cls, mapper: SemConvMapper) -> "SemConvSnapshot": ...   # introspect the live mapper
    @classmethod
    def load(cls, path: str) -> "SemConvSnapshot": ...                  # the committed baseline
    def diff(self, other: "SemConvSnapshot") -> "SnapshotDiff": ...     # per-op added/removed keys

# forgesight_testing.conformance — NEW case in the existing harness (feat-011)
def run_semconv_conformance(mapper_factory: Callable[[], SemConvMapper], *,
                            snapshot_path: str) -> None:
    """Assert the live mapper's emitted attribute-key set per span kind matches the
    committed snapshot at the declared pin. Fails (with a per-op key diff) on any
    drift — the P10 enforcement of ADR-0004's pin."""
```

**Stability contract** (the heart of this feature, P5 — published in the otel mapping
design doc and asserted by the snapshot):

| Part of the mapping | Stability | What it means for consumers |
|---|---|---|
| Span kinds (`INTERNAL`/`CLIENT`) per domain type | **stable** | safe to build on; changes are a major bump + ADR |
| `gen_ai.operation.name` values (`invoke_agent`, `chat`, `execute_tool`, `invoke_workflow`) | **stable** | the span-kind discriminator; locked |
| `forgesight.*` extensions (`forgesight.usage.cost_usd`, `forgesight.run.id`, `forgesight.semconv_version`) | **stable** | ForgeSight owns these; never squat `gen_ai.*` (P4, ADR-0005) |
| `gen_ai.usage.*` token attributes | **tracking-experimental** | follows the upstream spec; a rename triggers a `SEMCONV_RENAMES` window, not a silent break |
| `gen_ai.request.*` params, `gen_ai.response.*`, tool/MCP attrs | **tracking-experimental** | may be renamed/removed upstream; covered by the snapshot + window |
| Content attrs (`gen_ai.input.messages`, …) | **tracking-experimental + opt-in** | mid-migration upstream (span-attr vs event); P7-gated |

`SEMCONV_VERSION` / `SEMCONV_COMMIT` / `FORGESIGHT_SEMCONV_VERSION` are **stable**.
`SemConvCompat`, `SEMCONV_RENAMES`, `SemConvSnapshot`, and `run_semconv_conformance` are
**experimental** within 0.x (signature changes are changelog-called-out). No new SPI: the
mapper is still package-local to `forgesight-otel`, the snapshot check is a new case in
the locked feat-011 conformance harness.

### 4.3 Internal mechanics

**The pin flows: config → mapper → Resource.** The version/commit default to the built-in
`SEMCONV_VERSION` / `SEMCONV_COMMIT` constants; surfacing them in config lets ops *assert*
which revision a deploy targets and lets the value reach the Resource explicitly:

```
OTelExporter.__init__(semconv={version, commit, compat})
   │  version/commit default to SEMCONV_VERSION / SEMCONV_COMMIT (the built-in pin)
   │
   ├── SemConvMapper(compat=compat)                       # mapper is compat-aware
   └── TracerProvider(resource = Resource({
           service.name,
           forgesight.semconv_version = version,          # queryable on every span
           …resource_attributes }))
```

**Dual-emit during a migration window.** The mapper consults `compat` and the active
`SEMCONV_RENAMES` map. In `current` mode it emits only the pinned names — today's
behaviour, unchanged. In `dual` mode, for each renamed attribute it *additionally* writes
the previous key with the same value, so both names are present on the span:

```
SemConvMapper.attributes(record)                         # on the export worker (feat-003)
   for each (current_key, value) the mapper would emit:
       attrs[current_key] = value
       if compat == DUAL and current_key in SEMCONV_RENAMES:
           attrs[SEMCONV_RENAMES[current_key]] = value    # mirror to the previous name
   →  a backend keyed on the OLD name keeps working through the deprecation window;
      a backend on the NEW name already works. Neither breaks on the re-pin.
```

This is exactly the shape of the existing `emit_legacy_system` flag (which mirrors
`gen_ai.provider.name` → `gen_ai.system`), generalised from one hard-coded attribute to a
declared rename map. `emit_legacy_system` remains as the specific, already-shipped case;
`SEMCONV_RENAMES` + `dual` mode is the general mechanism for the *next* rename.

**The conformance snapshot is captured by introspection, asserted in CI.** The snapshot is
generated by driving the live `SemConvMapper` with one canonical `Record` per span kind and
recording the exact attribute-key set it emits (separating always-on, content-gated, and
legacy keys so the assertion is deterministic regardless of `capture_content` /
`emit_legacy_system`):

```
generate (CI helper, on intentional re-pin):
   for kind in {AGENT, WORKFLOW, LLM, TOOL, MCP, STEP}:
       rec = canonical_record(kind)
       base    = mapper.attributes(rec).keys()
       content = mapper.attributes(rec, capture_content=True).keys()  - base
       legacy  = mapper.attributes(rec, emit_legacy_system=True).keys() - base
   →  SemConvSnapshot{semconv_version, semconv_commit, base/content/legacy by op_name}
   →  written to semconv_snapshot.json, committed to the repo

assert (every CI run, via feat-011):
   live = SemConvSnapshot.capture(SemConvMapper())
   committed = SemConvSnapshot.load("semconv_snapshot.json")
   diff = committed.diff(live)
   if diff.has_changes(): FAIL with per-op added/removed keys
   →  an unintended change to the emitted set (a stray rename, a dropped attr) fails
      the build; an intentional re-pin requires regenerating + committing the snapshot,
      so the diff is reviewed in the PR (P10, NFR-7).
```

The snapshot keys on `semconv_version`: when a re-pin changes both the version *and* the
emitted set, the diff names exactly which attributes moved on which span kind — the
re-pin is reviewable, not a leap of faith.

**Why this is free of the hot path.** All of it lives on the export worker / in CI:
the mapper is already a pure `Record → attributes` function on the worker (feat-003,
never the hot path); dual-emit is an extra in-memory dict write per renamed key; the
snapshot capture and assertion run only in CI. No live-path cost, no I/O (P6).

### 4.4 Module packaging

- This feature **extends `forgesight-otel`** (feat-004) — it adds no new package. The
  `SemConvCompat` enum, the `SEMCONV_RENAMES` map, the compat-aware `SemConvMapper`
  constructor, and the `SemConvSnapshot` / `conformance.py` snapshot tooling all live in
  the existing `forgesight-otel`. It keeps depending only on `forgesight-core` + the OTel
  SDK + OTLP exporters — **no vendor SDK** (P1).
- The **conformance case** (`run_semconv_conformance`) lands in the existing feat-011
  harness namespace (`forgesight_testing.conformance` / re-exported via
  `forgesight.testing.conformance`), alongside the four `run_*_conformance` entry points.
  The committed `semconv_snapshot.json` lives with the `forgesight-otel` tests.

```bash
pip install forgesight-otel            # the pin + compat ship with the exporter
pip install --group dev forgesight-testing   # the snapshot conformance case (dev only)
```

```yaml
# forgesight.yaml
exporters:
  - name: otel
    config:
      endpoint: "http://otel-collector:4317"
      semconv:
        compat: "current"              # "current" | "dual"
```

**No new entry-point group.** The exporter still registers under the existing
`forgesight.exporters` group as `otel` (feat-004); the snapshot check is a CI test, not a
runtime plugin. No telemetry-path entry point is added.

### 4.5 Configuration

| Key (YAML under `exporters[].config.semconv`) | Env | Default | Validation |
|---|---|---|---|
| `version` | `FORGESIGHT_OTEL_SEMCONV_VERSION` | built-in `SEMCONV_VERSION` | non-empty; stamped on Resource as `forgesight.semconv_version` |
| `commit` | `FORGESIGHT_OTEL_SEMCONV_COMMIT` | built-in `SEMCONV_COMMIT` | non-empty `repo@sha` form |
| `compat` | `FORGESIGHT_OTEL_SEMCONV_COMPAT` | `current` | one of `current`, `dual` |

**Validation rules.** `compat` ∈ `{current, dual}` — `dual` is only meaningful while
`SEMCONV_RENAMES` is non-empty (a migration window is open); when it's empty, `dual`
behaves like `current` (no renames to mirror) and is accepted but a no-op, logged once at
`configure()`. `version` / `commit` are informational/auditing overrides — they do **not**
let a consumer remap attributes (the mapping is the SDK's controlled surface, §9); setting
them to a value that doesn't match the built-in pin is rejected at `configure()` unless it
*equals* the built-in pin, so the declared version can never lie about what the mapper
actually emits. Unknown keys under `semconv` are rejected at `configure()` (architecture
§8).

**Defaults.** `compat` defaults `current` (today's behaviour, unchanged); `version` /
`commit` default to the built-in constants so an unconfigured deploy still stamps the pin
on the Resource. The existing `emit_legacy_system` (feat-004) keeps its `false` default
and its specific `gen_ai.system` behaviour, independent of `semconv.compat`.

**Env overrides** (feat-010): `FORGESIGHT_OTEL_SEMCONV_COMPAT`,
`FORGESIGHT_OTEL_SEMCONV_VERSION`, `FORGESIGHT_OTEL_SEMCONV_COMMIT` — kwargs > env > YAML
(last-wins).

## 5. Plug-and-play & upgrade story

Nothing to add: the pin and the compat knob ship with `forgesight-otel` (feat-004), behind
defaults that reproduce today's behaviour exactly. A team that does nothing gets the
declared `forgesight.semconv_version` on the Resource and `compat: current` — identical
wire output to before, plus a queryable convention dimension. A team mid-migration sets
`semconv.compat: dual` (a config edit, no agent-code change) for the deprecation window,
then drops back to `current` once their backends are on the new names.

Upgrade safety is the entire point. When a ForgeSight minor re-pins `SEMCONV_COMMIT` and
follows an upstream rename: the snapshot diff makes the change reviewable in the PR (P10);
the renamed attribute lands in `SEMCONV_RENAMES` so backends can run `dual` for one minor
(P5, mirroring feat-004's one-minor back-compat policy); and the bumped
`forgesight.semconv_version` on the Resource tells backends exactly which revision a stream
targets. A consumer pinned to `-api` is untouched — they depend on the domain model, never
the raw names (ADR-0004). The stability contract (§4.2) says up front which attributes are
safe to build long-lived dashboards on and which are tracking-experimental, so no upgrade
is a surprise.

## 6. Cross-language parity

Identical across Python / TypeScript: the `forgesight.semconv_version` Resource attribute,
the `compat` semantics (`current` / `dual`) and the dual-emit rule, the `SEMCONV_RENAMES`
shape, the stability-contract table (which attribute groups are stable vs
tracking-experimental), and — crucially — the **conformance snapshot format and its CI
assertion** (a TS mapper and a Python mapper must emit the *same* attribute-key set per
span kind at the same pin; that's the parity anchor, mirroring feat-011's
conformance-as-parity model). Allowed to differ: idiomatic naming (`semconvCompat` ↔
`semconv.compat`, `fromFile` ↔ `load`), the snapshot file plumbing, and the CI-helper
invocation. Python lands first; the snapshot *contract* is what the TS port is measured
against.

## 7. Test strategy

- **Unit:** `compat=current` emits only the pinned names (byte-identical to feat-004's
  current output); `compat=dual` with a populated `SEMCONV_RENAMES` mirrors each renamed
  key to its previous name with the same value, and is a no-op when `SEMCONV_RENAMES` is
  empty; the declared `version`/`commit` reach the Resource as `forgesight.semconv_version`;
  config rejects a `version` that disagrees with the built-in pin.
- **Snapshot capture:** `SemConvSnapshot.capture` records the exact key set per span kind
  (AGENT/WORKFLOW/LLM/TOOL/MCP/STEP), correctly separating base / content-gated / legacy
  keys; `diff` reports per-op added/removed keys.
- **Conformance (P10, the headline):** `run_semconv_conformance` asserts the live mapper
  matches the committed `semconv_snapshot.json` and **fails** on drift — proven by a
  deliberately mutated mapper (a stray rename, a dropped attr) that must make the suite
  red, exactly like feat-011's known-bad meta-tests.
- **Integration:** export against OTel's `InMemorySpanExporter`; assert
  `forgesight.semconv_version` is on the Resource for every span; under `dual`, assert both
  the renamed and previous keys are present on the relevant span (feat-004's snapshot path).
- **Re-pin drill (the migration story end to end):** a fixture rename in `SEMCONV_RENAMES`
  → snapshot diff names the moved attribute on the right span kind → `dual` keeps a
  backend on the old name working → `current` after the window drops the old name. This is
  the §1 "silent dashboard break" scenario shown to be caught and survived.
- **Example:** a two-version rollout where one stream is `current` and one is `dual`, with
  the convention version queried off the Resource — the headline demo for "which
  convention is this, and how do I migrate without a flag-day."

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| A re-pin silently changes the emitted attribute set | The conformance snapshot fails CI on any drift; an intentional change requires regenerating + committing the snapshot, so the diff is reviewed in the PR (P10) |
| `dual` mode bloats spans with duplicate attributes indefinitely | `dual` is for a bounded deprecation window (one minor, mirroring feat-004); `SEMCONV_RENAMES` is emptied when the window closes, after which `dual` is a no-op and the old names stop |
| Declared `version`/`commit` could lie about what the mapper emits | Config rejects a `version`/`commit` that disagrees with the built-in pin; the pin is informational/auditing, never a remap lever — the snapshot ties the version to the actual emitted set |
| Consumers want to remap attributes themselves | Out of scope (§9) — the mapping is the SDK's controlled surface (P4); arbitrary user remapping re-creates the fragmentation feat-004 ended |
| The stability line (stable vs tracking-experimental) is a judgement call | The contract table (§4.2) is published in the otel mapping design doc and asserted by the snapshot; moving an attribute between stable/experimental is a changelog-called-out, reviewed change |
| Overlap with the existing `emit_legacy_system` flag | `emit_legacy_system` stays as the already-shipped specific case (`gen_ai.system`); `SEMCONV_RENAMES` + `dual` is the *general* mechanism for future renames — they compose, neither replaces the other |
| Snapshot churns on every unrelated attribute addition | The snapshot is keyed on attribute *keys* per span kind; adding an optional attribute is a reviewed snapshot update (an additive diff), not a silent change — which is the point |

## 9. Out of scope

- **Inventing or extending the GenAI conventions themselves.** ForgeSight *layers on* the
  upstream spec; it does not own a competing convention or add `gen_ai.*` identifiers the
  spec hasn't shipped (P4, ADR-0004, ADR-0005). The only sanctioned additions are the
  namespaced `forgesight.*` extensions (cost, run id, the version stamp). This feature
  pins and verifies the *existing* mapping; it does not author new attributes.
- **Arbitrary user-defined attribute remapping.** The mapping is the SDK's controlled
  surface — the whole value of the single seam (P4) is that every consumer emits the same
  names. The `version`/`commit`/`compat` knobs are auditing + a migration window, not a
  hook for a consumer to rename `gen_ai.usage.input_tokens` to their own key. A team that
  needs a different name does it in their backend, not in the SDK's wire format.
- **Forward-compatibility promises for experimental attributes.** The stability contract is
  explicit: `gen_ai.*` token/request/response/tool/content attributes are
  *tracking-experimental* — the upstream spec may rename or **remove** them, and when it
  does ForgeSight follows (with a snapshot diff and, for renames, a `dual` window). This
  feature does not promise an experimental attribute will exist forever; it promises the
  *change* will be visible and, where possible, non-breaking.
- **A general schema/convention registry.** ForgeSight declares and verifies *its own*
  pinned mapping; it is not a registry of every convention version every consumer might
  want, nor a service that serves convention definitions. The snapshot is one committed
  baseline per pin, not a versioned catalogue.
- **Tracking the upstream spec automatically.** Re-pinning `SEMCONV_COMMIT` stays a
  deliberate, reviewed `forgesight-otel` change (ADR-0004 Option A — we lag `main` by
  design). This feature makes a re-pin *safe and reviewable*; it does not automate it.
- **Multi-version emission beyond the single dual-emit window.** `dual` emits the current
  + the *immediately previous* names for one migration window. It is not an N-version
  compatibility matrix emitting every historical name a consumer ever depended on.

## 10. References

- [`../adr/0004-pin-and-isolate-genai-semconv.md`](../adr/0004-pin-and-isolate-genai-semconv.md) — pin + isolate + version (this feature formalises/enforces its three pillars)
- [`../adr/0005-cost-as-namespaced-extension.md`](../adr/0005-cost-as-namespaced-extension.md) — the `forgesight.*` extensions that are *stable* in the contract
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) — the canonical mapping; §4.1 (pinning/isolation), §6 (migration), §7 (rename risk) this feature operationalises
- [`../design/design-principles.md`](../design/design-principles.md) — P4 (OTel-first; the single mapping seam), P1 (vendor-neutral; stays in `forgesight-otel`), P5 (stable contracts; the stability table), P10 (conformance over trust; the snapshot)
- [`../requirements.md`](../requirements.md) — FR-11 (multi-backend export — the pin/window protect every backend at once), NFR-7 (quality bar; conformance suite every implementation passes)
- feat-004 (OTel exporter & semconv mapping — `SemConvMapper`, `SEMCONV_VERSION`/`SEMCONV_COMMIT`, `emit_legacy_system`; this feature extends it), feat-011 (testing & conformance harness — the snapshot check is a new case)
- OpenTelemetry GenAI semconv: <https://github.com/open-telemetry/semantic-conventions-genai>
