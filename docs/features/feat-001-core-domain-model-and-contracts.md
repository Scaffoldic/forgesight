# feat-001: Core domain model & SPI contracts

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-001 |
| **Title** | Core domain model & SPI contracts |
| **Status** | `shipped` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1.0 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-api` (import root `forgesight_api`) |
| **Depends on** | `none` |
| **Blocks** | feat-002, feat-003, feat-004, feat-005, feat-006, feat-007, feat-008, feat-009, feat-010, feat-011 |

---

## 1. Why this feature

Every team building agents re-invents the same three things on day one: a shape
for "what the agent did" (a run, its LLM calls, its tools), a way to ship that
somewhere, and a token-to-cost calculation. Because each team invents its own
shape, **no two agents are comparable** — one logs `tokens_in`, the next
`prompt_tokens`, the third `input`. A platform team that wants fleet-wide cost
or a single dashboard across ten agents has to write ten adapters first.

The pain is concrete and recurring:

- An SRE asks "which run cost the most yesterday?" and there is no `run_id` that
  means the same thing across two services, no `cost_usd` field with agreed
  semantics, no `status` enum that distinguishes `budget_exceeded` from `error`.
- A backend swap (Langfuse → ClickHouse) becomes a rewrite because the agent
  code is coupled to a Langfuse object, not to a neutral record.
- AgentForge (the framework) wants to emit telemetry but **cannot afford a
  vendor dependency** — it needs a contract it can pin to that drags in zero
  backend SDKs.

feat-001 is the answer to all three: one small, frozen, OTel-shaped domain
model plus the four extension contracts (SPIs) that everything else in the SDK
implements. It is the *root* of the dependency graph — nothing ships before it,
because every other feature is defined in terms of these types.

## 2. Why this belongs in the SDK core (vs each agent/team rolling its own)

This is the load-bearing case for the whole project, so it has to be airtight.

- **One shared model is the only thing that makes runs comparable.** The value
  of the SDK is that an `AgentRun` from a LangGraph agent, a CrewAI agent, and a
  hand-written script all carry the same fields with the same meaning. That
  comparability is impossible if the model lives in each agent — it only exists
  if it ships once, in a package everyone depends on. *Before:* each team's
  `run_id` is a different format and their cost is computed differently, so the
  org's "total agent spend" is a guess. *After:* `run_id` is always a ULID,
  `cost_usd` is always USD computed the same way, and a single query aggregates
  the fleet.
- **The four SPIs are the entire extension surface — there is no fifth way**
  ([architecture §6](../design/architecture.md#6-extension-points)). If the SDK
  doesn't own them, every backend invents its own integration point and the
  surface fragments exactly as observability has fragmented everywhere else. A
  shipped contract means a "Langfuse exporter" is *the same kind of object* as a
  ClickHouse exporter and a custom in-house sink — discoverable by name,
  swappable by config, testable by one conformance suite (feat-011, P10).
- **Vendor neutrality is a structural invariant, not a coding-style wish**
  ([ADR-0002](../adr/0002-three-tier-vendor-neutral-packaging.md), P1).
  `forgesight-api` is the leaf of the dependency tree: it imports nothing but
  the stdlib and `typing-extensions`. AgentForge depends on `-api` **only**, so
  the framework never inherits a backend SDK transitively. That guarantee can
  only be made by a package whose dependency closure is frozen by design — it
  cannot be retrofitted onto a model that each agent owns.
- **Contract stability is a promise the SDK keeps so others can pin to it**
  ([ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md), P5). The model and
  SPIs are *locked surface*: adding an optional field with a safe default is a
  minor bump; renaming a field or changing an SPI signature is a major bump with
  an ADR. AgentForge and third parties pin to `-api` and trust that a minor
  upgrade won't break them. A model scattered across agents has no such promise.

**Anti-pattern if we leave it to each agent:** the OpenLLMetry / OpenInference /
Langfuse-SDK / Logfire landscape, replayed inside one company — N incompatible
schemas, N cost calculations, every backend integration lagging the product,
and a framework that can't emit telemetry without picking a vendor.

If the honest answer were "this could live in a derived agent," we'd push back —
but it can't: the comparability and the zero-vendor-dependency guarantees only
exist if the model ships once, centrally, with a frozen dependency closure.

## 3. How agents/teams consuming the SDK benefit

- **An agent author who imports `forgesight_api` gets a vocabulary for
  free.** `AgentRun`, `LLMCall`, `ToolCall`, `MCPCall`, `WorkflowRun`, `Step`,
  `TokenUsage`, `RunStatus`, `Kind` — the exact concepts they were about to
  define, already named, typed, and OTel-aligned. Zero lines of schema design.
- **A backend author writes ~30 lines, not a fork.** Implement
  `TelemetryExporter` (three methods), register an entry point, done — it now
  works with every agent on the SDK. *Before:* fork the SDK or monkey-patch.
  *After:* `class MyExporter: def export(self, records): ...` plus one
  `pyproject.toml` line.
- **Swapping a backend is a config change, not a code change.** Because agent
  code touches only the neutral `Record`/SPI surface, moving from console to
  OTLP to ClickHouse never edits the agent. The decision is deferred to deploy
  time and owned by the platform team.
- **AgentForge (and any framework) pins to `-api` and inherits no vendor.** A
  framework can emit telemetry through these contracts and let the *deploying*
  team choose the backend — no lock-in, which is AgentForge's own requirement
  ([architecture §11](../design/architecture.md#11-relationship-to-agentforge)).
- **Cost control / governance is a `PricingProvider` or `Interceptor`, not a
  patch.** A FinOps team adds a budget check by implementing one SPI method —
  the model already carries `TokenUsage` and `cost_usd`, so the data is there to
  enforce against (feat-020 builds directly on this surface).

## 4. Feature specifications

### 4.1 User-facing experience

`forgesight-api` is contracts, not runtime — most app authors never import
it directly (they use the `telemetry` facade from feat-002). The people who
*do* import it are **backend authors** and **framework integrators**. The
minimal thing each writes:

```python
# python — write a custom exporter against the locked SPI
from collections.abc import Sequence
from forgesight_api import TelemetryExporter, Record, ExportResult

class StdoutExporter:                       # structurally a TelemetryExporter
    def export(self, records: Sequence[Record]) -> ExportResult:
        for r in records:
            print(r.kind, r.run_id, r.attributes)
        return ExportResult.SUCCESS         # never raises (P6)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        pass

# python — a custom pricing provider
from forgesight_api import PricingProvider, TokenUsage

class FlatRatePricer:                        # structurally a PricingProvider
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        return usage.total * 1e-6
```

```python
# python — a framework integrator builds the immutable records the SDK exports
from forgesight_api import AgentRun, RunStatus, LLMCall, TokenUsage

run = AgentRun(
    agent_name="issue-classifier", agent_version="1.2.0",
    run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",   # ULID
    context_id=None, trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    parent_run_id=None, status=RunStatus.RUNNING,
    start_unix_nanos=1_718_000_000_000_000_000, end_unix_nanos=None,
)
call = LLMCall(provider="anthropic", request_model="claude-sonnet-4-5",
               usage=TokenUsage(input=1200, output=350, cache_read=800))
```

Because every SPI is a `runtime_checkable` `Protocol`, **no base class import or
inheritance is required** — a plain class with the right methods *is* an
exporter. `isinstance(obj, TelemetryExporter)` works for registration-time
validation.

```typescript
// @agentforge/sdk-api — same contract, idiomatic interface
import { TelemetryExporter, Record, ExportResult } from '@agentforge/sdk-api';

class StdoutExporter implements TelemetryExporter {
  export(records: Record[]): ExportResult {
    for (const r of records) console.log(r.kind, r.runId, r.attributes);
    return ExportResult.SUCCESS;
  }
  async forceFlush(timeoutMillis = 30_000): Promise<boolean> { return true; }
  async shutdown(timeoutMillis = 30_000): Promise<void> {}
}
```

### 4.2 Public API / contract

The complete locked surface. **Stable (locked)** unless explicitly annotated
**experimental**. Changing a locked symbol is a major bump + ADR
([ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md), P5). Adding an
optional field with a safe default is a minor bump.

#### Enums — `forgesight_api/model.py` — **locked**

```python
from enum import Enum

class RunStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    GUARDRAIL = "guardrail"

class Kind(str, Enum):
    WORKFLOW = "workflow"
    AGENT = "agent"
    STEP = "step"
    LLM = "llm"
    TOOL = "tool"
    MCP = "mcp"
```

Both subclass `str` so they serialise to their value with no custom encoder and
compare equal to the wire string — important for exporters that emit JSON.

#### Value types — `forgesight_api/model.py` — **locked**

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    reasoning: int = 0

    @property
    def total(self) -> int:
        return (self.input + self.output + self.cache_read
                + self.cache_creation + self.reasoning)
```

`TokenUsage` is `frozen` — usage is a fact about a completed call, never mutated.
Field names map deterministically onto the GenAI token attributes
([otel-semantic-conventions §4.3](../design/otel-semantic-conventions.md#43-attribute-mapping)).

#### Operation models — `forgesight_api/model.py` — **locked**

These are the *builder-facing* mutable views used while an operation is in
flight (the runtime fills `status`, `duration_ms`, `cost_usd` on completion).
They are converted to immutable `Record`s before export (§4.3).

```python
@dataclass(slots=True)
class LLMCall:
    provider: str                          # → gen_ai.provider.name
    request_model: str                     # → gen_ai.request.model
    response_model: str | None = None      # → gen_ai.response.model
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None          # → forgesight.usage.cost_usd; None until priced
    finish_reasons: tuple[str, ...] = ()   # → gen_ai.response.finish_reasons
    latency_ms: float | None = None
    time_to_first_chunk_ms: float | None = None
    response_id: str | None = None
    params: dict[str, object] = field(default_factory=dict)  # temperature, max_tokens, top_p…
    # content (messages) is OPT-IN and lives on a separate, gated field (P7, feat-008)
    content: "Content | None" = None       # populated only when capture_content is on

@dataclass(slots=True)
class ToolCall:
    name: str                              # → gen_ai.tool.name
    tool_type: str = "function"            # → gen_ai.tool.type (open set: function/extension/datastore/…)
    call_id: str | None = None             # → gen_ai.tool.call.id
    description: str | None = None         # → gen_ai.tool.description
    status: RunStatus = RunStatus.RUNNING
    duration_ms: float | None = None

@dataclass(slots=True)
class MCPCall:
    server: str
    method: str                            # → mcp.method.name (e.g. tools/call)
    tool: str | None = None                # → gen_ai.tool.name when method == tools/call
    session_id: str | None = None          # → mcp.session.id
    protocol_version: str | None = None    # → mcp.protocol.version
    status: RunStatus = RunStatus.RUNNING
    duration_ms: float | None = None

@dataclass(slots=True)
class Step:
    name: str
    kind: Kind = Kind.STEP                 # → INTERNAL span
    status: RunStatus = RunStatus.RUNNING
    start_unix_nanos: int = 0
    end_unix_nanos: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None: ...

@dataclass(slots=True)
class AgentRun:
    agent_name: str
    agent_version: str | None
    run_id: str                            # ULID (see §4.3)
    context_id: str | None                 # → gen_ai.conversation.id when a real session exists
    trace_id: str                          # W3C 16-byte hex trace id
    parent_run_id: str | None              # links nested / spawned runs
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    metadata: dict[str, object] = field(default_factory=dict)  # business metadata (FR-5)

    @property
    def duration_ms(self) -> float | None: ...

@dataclass(slots=True)
class WorkflowRun:
    workflow_name: str
    run_id: str                            # ULID
    trace_id: str
    parent_run_id: str | None
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None: ...
```

`Content` (the opt-in message-capture container) is **experimental** in 0.1 —
its exact shape tracks the GenAI content-capture migration
([otel-semantic-conventions §4.3](../design/otel-semantic-conventions.md#43-attribute-mapping));
the *gate* (off by default, P7) is locked, the *shape* may change.

#### Exporter-facing value types — `forgesight_api/record.py` — **locked**

The pipeline converts a live operation model into an **immutable `Record`**
before it ever crosses the queue boundary. Exporters consume `Record`s, never
live objects ([architecture §3](../design/architecture.md#3-key-concepts)).

```python
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from collections.abc import Mapping

@dataclass(frozen=True, slots=True)
class Record:
    """The immutable, exporter-facing snapshot of one operation start/end."""
    kind: Kind
    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str                              # span name (post semconv mapping is the exporter's job)
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    attributes: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    # one of the following is set depending on kind:
    llm: LLMCall | None = None
    tool: ToolCall | None = None
    mcp: MCPCall | None = None

    @property
    def duration_ms(self) -> float | None: ...

class ExportResult(Enum):                  # mirrors OTel SpanExportResult
    SUCCESS = 0
    FAILURE = 1

class EventType(str, Enum):                # open set (FR-8)
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    LLM_EXECUTED = "llm_executed"
    TOOL_EXECUTED = "tool_executed"
    MCP_EXECUTED = "mcp_executed"

@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Delivered to every EventListener in order. Carries the record it describes."""
    type: EventType
    run_id: str
    unix_nanos: int
    record: Record | None = None
    attributes: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
```

`Record`, `LifecycleEvent`, `ExportResult`, `EventType` are **locked**. `Record`
is `frozen` with a read-only `attributes` mapping so an exporter or interceptor
cannot mutate state shared with another exporter — fault isolation starts in the
type system (P6).

#### The four SPIs — `forgesight_api/spi.py` — **locked**

```python
from typing import Protocol, runtime_checkable
from collections.abc import Sequence

@runtime_checkable
class TelemetryExporter(Protocol):
    """Ships records to ONE backend. Called by the pipeline worker, never on the hot path."""
    def export(self, records: Sequence[Record]) -> ExportResult: ...   # MUST NOT raise (P6)
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...

@runtime_checkable
class Interceptor(Protocol):
    """Mutate / redact / veto a record before export. Runs in registration order on the hot path."""
    def intercept(self, record: Record) -> Record | None: ...          # None ⇒ drop (counted)

@runtime_checkable
class EventListener(Protocol):
    """Side-effect subscriber to lifecycle events. Isolated from the run (FR-8, P6)."""
    def on_event(self, event: LifecycleEvent) -> None: ...

@runtime_checkable
class PricingProvider(Protocol):
    """Resolve cost. Returns None for unknown models (degrade gracefully, FR-9)."""
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None: ...
```

Contract guarantees that are part of the locked surface:

- `export()` **returns** `ExportResult.FAILURE`; it does **not** raise. The
  worker guards with try/except as defence in depth, but raising is a contract
  violation (P6,
  [exporter-pipeline §4.4](../design/exporter-pipeline.md#44-fault-isolation-p6)).
- `Interceptor.intercept` returning `None` **drops** the record (counted in
  `sdk_records_dropped_total`); returning a new `Record` replaces it; returning
  the same one passes it through. Interceptors run in registration order; a
  raising interceptor is skipped, the chain continues (feat-008).
- `EventListener.on_event` is fire-and-forget from the run's perspective; a
  raising listener is logged and isolated (feat-007).
- `PricingProvider.price` returns `None` for unknown models — never raises, never
  fabricates (FR-9, [cost-model §4.1](../design/cost-model.md#41-the-spi)).

#### TypeScript parity sketch — `@agentforge/sdk-api`

```typescript
export enum ExportResult { SUCCESS, FAILURE }

export interface TelemetryExporter {
  export(records: Record[]): ExportResult | Promise<ExportResult>;
  forceFlush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<void>;
}
export interface Interceptor { intercept(record: Record): Record | null; }
export interface EventListener { onEvent(event: LifecycleEvent): void; }
export interface PricingProvider {
  price(provider: string, model: string, usage: TokenUsage): number | null;
}
```

Both languages declare the *same* contract; idiom differs (`Protocol` vs
`interface`, `runtime_checkable` vs structural typing,
`dataclass(frozen=True)` vs `readonly`), semantics do not
([architecture §10](../design/architecture.md#10-cross-language-parity)).

### 4.3 Internal mechanics

`forgesight-api` has **no runtime mechanics** — it is contracts and value
types with no I/O, no threads, no network. The two mechanics that *are* part of
this package's responsibility:

**ULID run-ids.** `run_id` is a [ULID](https://github.com/ulid/spec): 128-bit,
lexicographically sortable by creation time, Crockford base32, 26 chars. Chosen
over UUIDv4 because sortability means "most recent runs" is an index range scan,
not a timestamp join, in every backend. The *generator* lives in `-core`
(feat-002, so `-api` stays I/O-free), but the **format is fixed here** as the
contract: a valid `run_id` is a 26-char Crockford-base32 ULID. `trace_id` is a
W3C 16-byte (32-hex-char) trace id, distinct from `run_id` (one trace can carry
nested runs with distinct `run_id`s but a shared `trace_id`).

**Live model → immutable `Record`.** The boundary between "mutable while in
flight" and "frozen for export" is the key invariant:

```
   live operation (mutable)            immutable snapshot (frozen)
   ─────────────────────────           ───────────────────────────
   AgentRun / LLMCall / ...    ──┐
     runtime fills status,       │  build_record()   ┌─ Record (frozen, slots)
     duration, cost on exit      ├───────────────────┤    attributes: MappingProxyType
                                 │   (in feat-002)    └─ crosses queue → exporters
   Step / WorkflowRun         ──┘
```

The live models are mutable so the runtime can fill terminal fields cheaply on
the hot path. The `Record` is `frozen` + `slots` + read-only `attributes` so
that once it crosses into the pipeline, **no exporter or interceptor can mutate
state another exporter will see** — immutability is how fault isolation (P6) is
enforced structurally, not by convention.

**Why `Protocol` over ABC** ([ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md)):
structural typing means a backend author writes a plain class with no import-time
coupling to `-api`'s base classes; `runtime_checkable` still allows
`isinstance` validation at registration. An exporter from a package that doesn't
even import `forgesight_api` is still a valid exporter — maximally
plug-and-play (P2).

### 4.4 Module packaging

- **Lives in:** `forgesight-api` — the leaf tier, always installed
  transitively by `-core`, `-sdk`, and every integration
  ([architecture §5](../design/architecture.md#5-package-model-three-tiers--integrations),
  [ADR-0002](../adr/0002-three-tier-vendor-neutral-packaging.md)).
- **Import root:** `forgesight_api`.
- **Dependencies (locked, CI-enforced via import-linter):** stdlib +
  `typing-extensions` **only**. No `opentelemetry-*`, no vendor SDK, no `-core`.
  `-api` imports nothing from `-core` or any integration — it is the leaf, and a
  PR that adds a dependency here fails CI (P1).
- **pip install:** rarely installed directly; pulled in transitively.

  ```bash
  pip install forgesight-api      # only when pinning the contract directly
  ```

  AgentForge (and any framework) does exactly this: depends on
  `forgesight-api` only, emits through it, inherits zero vendor deps
  ([architecture §11](../design/architecture.md#11-relationship-to-agentforge)).
- **Entry points:** none defined *by* this package — it *defines* the SPI types
  that the entry-point groups resolve to. Implementations register under
  `forgesight.exporters` / `forgesight.interceptors` /
  `forgesight.listeners` / `forgesight.pricing` (the loader is feat-010).

### 4.5 Configuration

**None.** This package is pure contracts — it reads no environment variables and
no config file. The `FORGESIGHT_*` knobs are introduced by the runtime
(feat-002), pipeline (feat-003), and config bootstrap (feat-010) that *consume*
these types. Listing config here would violate the package's "no I/O, no
behaviour" charter.

## 5. Plug-and-play & upgrade story

`forgesight-api` is always installed (it's the leaf every other package
depends on), so there is no "add it later" step — it's present the moment any
SDK package is.

**Upgrade safety is the whole point of this feature** (P5,
[ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md)):

- **Minor bump:** may add an optional field (safe default) to a model, a new
  `EventType`/`Kind`/`RunStatus` member (open sets), or a new SPI with a default.
  Existing exporters/interceptors keep working untouched.
- **Major bump:** removing/renaming a field, changing an SPI signature, or
  tightening an enum. Requires an ADR and a deprecation window. AgentForge and
  third parties pin to a minor of `-api` and are guaranteed source compatibility
  within it.
- New optional fields are added *after* existing ones with defaults so
  positional construction in older callers still compiles.

## 6. Cross-language parity

**Identical across Python / TypeScript (and future Java / Go):** the six domain
models, `TokenUsage`, the `RunStatus`/`Kind`/`EventType` enums, `Record`,
`LifecycleEvent`, `ExportResult`, and the four SPI signatures — same field names
(modulo `snake_case` ↔ `camelCase`), same semantics, same locked/experimental
status ([architecture §10](../design/architecture.md#10-cross-language-parity)).

**Allowed to differ:** the typing mechanism (`Protocol` + `runtime_checkable` vs
TS `interface` + structural typing), immutability mechanism
(`dataclass(frozen=True, slots=True)` vs `readonly` + `Object.freeze`),
`MappingProxyType` vs `ReadonlyMap`, and `export()` being optionally
`Promise`-returning in TS (Python is sync; both are non-blocking by the pipeline
running them off the hot path).

**Deferred in TS:** nothing in *this* feature — the contract is the parity
anchor; TS targets it from the start of its 0.x line (Python ships first per
[ADR-0008](../adr/0008-python-first-multilanguage-parity.md)).

## 7. Test strategy

- **Unit tests** — enum values match their wire strings; `TokenUsage.total`
  sums all five fields; `duration_ms` is `None` while `end_unix_nanos is None`
  and correct once set; `frozen` types reject mutation (`FrozenInstanceError`);
  `Record.attributes` is a read-only mapping.
- **ULID format conformance** — generated `run_id`s are 26-char Crockford
  base32, monotonically sortable by creation order, unique under tight loops.
- **Protocol conformance** — a minimal class implementing each SPI passes
  `isinstance(obj, TelemetryExporter)` etc.; a class missing a method fails it.
- **Serialisation** — every model round-trips to JSON and back with no custom
  encoder (the `str`-enum + dataclass shapes guarantee it); exporters depend on
  this.
- **Conformance harness seed** — feat-011's per-SPI conformance suites are
  *defined against these contracts*; this feature ships the abstract test
  contract each suite specialises (P10).
- **Import-linter** — CI asserts `forgesight_api` imports nothing outside
  the stdlib + `typing-extensions` (enforces P1 / the dependency rule).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Locking the model too early, then needing a breaking change | Only `total`-shaped, OTel-anchored fields are locked; speculative fields (`Content`) ship **experimental**; open-set enums absorb new members without a break. |
| `Content` shape churns with the GenAI content-capture migration | `Content` is experimental in 0.1; the *gate* (off by default, P7) is locked, the *shape* tracks [otel-semantic-conventions §4.3](../design/otel-semantic-conventions.md#43-attribute-mapping). |
| `Protocol` structural typing hides a missing method until runtime | `runtime_checkable` `isinstance` check at registration (feat-010) fails fast at `configure()`, not mid-run. |
| ULID dependency footprint | ULID generation is ~30 lines of pure Python in `-core`; no third-party dep needed in `-api` (which only fixes the *format*). |
| Is `PricingProvider` locked in v0.1 or experimental? | **Locked** — cost is core SDK value; the design-principles open question is resolved by [ADR-0005](../adr/0005-cost-as-namespaced-extension.md). |
| Mutable live models vs immutable records — two shapes to maintain | Deliberate: mutable for cheap hot-path fill, frozen for safe export. The `build_record()` boundary (feat-002) is the single conversion point. |

## 9. Out of scope

- **Any I/O, runtime, or behaviour.** Building records, propagating context,
  generating ULIDs, running the pipeline — all live in `-core` (feat-002/003).
  This package is types only.
- **The OTel attribute mapping.** Which `Record` field becomes which
  `gen_ai.*` attribute is feat-004; `-api` only fixes the *domain* field names
  ([otel-semantic-conventions](../design/otel-semantic-conventions.md)).
- **The pricing table / cost computation.** The `PricingProvider` *contract* is
  here; the shipped `TablePricingProvider` and the table are feat-006.
- **Config keys.** Introduced by the features that read them (feat-002/003/010).
- **A fifth extension point.** The four SPIs are the entire surface; no
  monkey-patching, class-swapping, or import hooks
  ([architecture §6](../design/architecture.md#6-extension-points)).

## 10. References

- [`architecture.md`](../design/architecture.md) §3 (key concepts), §4 (the
  contract), §5 (packaging), §6 (extension points)
- [`design-principles.md`](../design/design-principles.md) — P1, P5, P6, P7, P8
- [`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.3
  (attribute mapping the model fields anchor to)
- [`cost-model.md`](../design/cost-model.md) §4.1 (the `PricingProvider` SPI)
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.4 (why `export()`
  must not raise)
- [ADR-0002](../adr/0002-three-tier-vendor-neutral-packaging.md) — three-tier,
  vendor-neutral packaging
- [ADR-0005](../adr/0005-cost-as-namespaced-extension.md) — cost as a namespaced
  extension + pluggable pricing
- [ADR-0006](../adr/0006-protocol-spi-as-stable-surface.md) — Protocol-based SPIs
  as the stable surface
- [ADR-0008](../adr/0008-python-first-multilanguage-parity.md) — Python-first
  parity roadmap
- Consumers: [feat-002](./feat-002-telemetry-runtime-and-instrumentation-api.md)
  (runtime), [feat-003](./feat-003-async-export-pipeline.md) (pipeline),
  feat-004 (OTel mapping), feat-006 (cost), feat-007 (events), feat-008
  (interceptors), feat-011 (conformance)
- Prior art: OpenLLMetry (Traceloop), OpenInference (Arize Phoenix), Langfuse
  SDK, Pydantic Logfire — each couples its model to a vendor; this feature is
  the neutral core they lack.

---

## Implementation status

**Status: shipped (Python).** Landed via [PR #1](https://github.com/Scaffoldic/forgesight/pull/1) on `main` (CI green on Python 3.11/3.12/3.13, 100% coverage).

| Module | Scope |
|---|---|
| `forgesight_api/model.py` | `RunStatus` + `Kind` enums; `TokenUsage` (frozen, `.total`); `Content` (experimental); `LLMCall` / `ToolCall` / `MCPCall` / `Step` / `AgentRun` / `WorkflowRun` operation models with `duration_ms`. |
| `forgesight_api/record.py` | Immutable `Record` (frozen, read-only `attributes` via `MappingProxyType`, `duration_ms`); `ExportResult`; `EventType` (open set); `LifecycleEvent`. |
| `forgesight_api/spi.py` | The four `runtime_checkable` Protocols: `TelemetryExporter`, `Interceptor`, `EventListener`, `PricingProvider`. |
| `forgesight_api/ids.py` | `new_ulid` / `is_valid_ulid` (26-char Crockford base32) and `new_trace_id` / `is_valid_trace_id` (W3C 32-hex). |
| tests | 42 unit tests across model / record / ids / spi; **100% coverage**; `mypy --strict` clean; `ruff` clean. |

### Deviations from this spec

- **`StrEnum` instead of `class X(str, Enum)`.** §4.2 sketched `(str, Enum)`; the
  implementation uses `enum.StrEnum` (available on the ≥3.11 floor, ADR-0008).
  Same wire-serialisation and string-equality semantics, cleaner `str()`, and it
  satisfies the linter without a suppression.
- **ULID generator co-located in `forgesight-api`.** §4.3 said the generator would
  live in `-core` to keep `-api` "I/O-free". The implementation puts both
  `new_ulid()` and `is_valid_ulid()` in `forgesight_api/ids.py`: ULID generation is
  ~40 lines of pure stdlib (`time`, `os.urandom`) with no network/disk I/O and no
  third-party dependency, so it does not violate the leaf-package charter — and it
  makes the contract self-contained and immediately testable (spec §7 lists ULID
  format conformance as a feat-001 test). `-core` will reuse this helper.

### Not yet implemented

- TypeScript port (`@agentforge/sdk-api`) — Python-first (ADR-0008).
- `import-linter` dependency-rule CI check (spec §7) — deferred to feat-010's
  config/CI hardening; for now the dependency is enforced by `forgesight-api`'s
  `pyproject.toml` declaring only `typing-extensions`.

## Runbook

### How do I write a custom exporter?

A plain class with three methods *is* a `TelemetryExporter` (structural Protocol —
no base class to import):

```python
from collections.abc import Sequence
from forgesight_api import TelemetryExporter, Record, ExportResult

class StdoutExporter:
    def export(self, records: Sequence[Record]) -> ExportResult:
        for r in records:
            print(r.kind, r.run_id, r.name)
        return ExportResult.SUCCESS          # return FAILURE on error — never raise (P6)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        pass

assert isinstance(StdoutExporter(), TelemetryExporter)   # registration-time check
```

Register it via the `forgesight.exporters` entry point (loader lands in feat-010).

### How do I write a custom pricing provider?

```python
from forgesight_api import PricingProvider, TokenUsage

class FlatRatePricer:
    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        return usage.total * 1e-6        # return None for unknown models — never raise
```

### How do I generate a run id?

```python
from forgesight_api import new_ulid, new_trace_id
run_id = new_ulid()        # 26-char Crockford base32, sorts by creation time
trace_id = new_trace_id()  # 32-hex W3C trace id
```
