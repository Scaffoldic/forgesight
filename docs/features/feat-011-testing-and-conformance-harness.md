# feat-011: Testing & conformance harness

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-011 |
| **Title** | Testing & conformance harness (in-memory exporter, span-tree assertions, per-SPI conformance suites) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` (the `forgesight.testing` namespace), `forgesight-testing` |
| **Depends on** | feat-001, feat-002, feat-003 |
| **Blocks** | none |

---

## 1. Why this feature

Two distinct populations need to test against this SDK, and both hit a wall
without first-class tooling.

**Agent authors** want to assert *what their agent recorded* — "did the run
produce a `chat` span under the agent span with the right token counts and cost?"
Without help, they either mock the entire instrumentation API (brittle, tests the
mock not the SDK) or stand up a real OTLP collector in CI (slow, flaky, async).
What they need is a synchronous, in-memory sink and ergonomic assertions over the
span tree — so a test is `assert_span_tree(...)`, not 50 lines of collector
plumbing.

**Integration authors** (anyone shipping a `TelemetryExporter`, `Interceptor`,
`EventListener`, or `PricingProvider` — first-party *and* third-party) face a
worse problem. The whole architecture rests on N integrations honoring **one**
contract (P2/P5). But a Protocol is a *structural* promise — "has an `export`
method" — not a *behavioural* one. Nothing stops a third-party exporter from
*raising* out of `export()` (violating P6 and taking down the agent), or an
interceptor from mutating a caller-held record, or a pricing provider from
throwing on an unknown model instead of returning `None`. Structural typing can't
catch any of that. The only thing that keeps the ecosystem honest is a shared
behavioural test suite every implementation must pass.

This feature ships both: the `InMemoryExporter` + span-tree assertions + fixtures
for agent authors, and — critically (P10) — the per-SPI **conformance suites**
that turn each SPI's prose contract into executable tests every implementation
runs.

## 2. Why this belongs in the SDK

- **Conformance-over-trust is a stated principle (P10), and only the SDK can
  define the contract.** "A Langfuse exporter is only an exporter if it passes
  the exporter conformance tests" is meaningless unless the *SDK* owns and ships
  those tests. The contract author must write the contract's tests; a third party
  can't be trusted to define what "honors the SPI" means — that's exactly the
  drift the suite prevents. NFR-7 makes it a hard requirement: *every SPI ships a
  conformance suite every implementation must pass.*
- **The behavioural invariants are framework invariants.** "`export()` returns
  FAILURE, never raises" (P6), "`intercept` returning `None` drops" (feat-008),
  "a listener never affects the run" (feat-007), "unknown model → `None`, not an
  error" (feat-006) — these are *the SDK's* guarantees, the ones AgentForge and
  agents pin to (P5). They have to be tested by the SDK, applied uniformly to all
  N implementations, or the guarantees are fiction the moment integration N+1
  ships.
- **Deterministic test ergonomics are a baseline every agent author deserves.**
  Async, batched, fault-isolated export (the design that makes prod safe) is the
  enemy of a simple test. The SDK has to provide the synchronous in-memory path
  and a deterministic flush, or every agent author re-invents a flaky one and the
  "< 90% coverage" bar (NFR-7) is unreachable for downstream agents.
- **The anti-pattern if we leave it out:** each integration is tested (or not)
  against its author's *interpretation* of the SPI; contract drift accumulates;
  one exporter raises and the "fault-isolated" promise breaks in the field; agent
  authors mock the SDK and test their mocks. The whole "N integrations, one
  contract" architecture quietly fails.

## 3. How consuming agents/teams benefit

- **Before:** to test that their agent records the right trace, an author spins up
  a Phoenix/OTLP collector in CI, waits on async export, and gets flaky timeouts.
  ~60 lines of fixture, slow, intermittently red. **After:** a pytest fixture
  hands them an `InMemoryExporter`; `assert_span_tree(...)` checks the shape; a
  deterministic flush means no sleeps. ~5 lines, fast, deterministic.
- **Before:** a team writing a custom ClickHouse exporter has no way to know
  they've honored the contract until it misbehaves in prod (raises out of
  `export`, stalls the agent). **After:** they drop `run_exporter_conformance`
  into their test file; it asserts non-raising, flush/shutdown idempotency, and
  batch handling against *their* exporter. Green = contract-honest, before a
  single record ships.
- **Find spans by what they are, not by index.** `find_span(op="chat")` /
  `find_spans(name="execute_tool …")` — assert on the LLM call without caring
  where it landed in the list.
- **Record/usage fixtures save boilerplate.** Pre-built `LLMCall` / `ToolCall` /
  `TokenUsage` factories mean a redaction test or a pricing test is two lines, not
  twenty.
- **Third-party integrations stay trustworthy.** Because every shipped *and*
  third-party implementation runs the *same* suite, a platform team can adopt a
  community exporter knowing it passed the SDK's own conformance bar (P10) — the
  suite is the trust mechanism, not a README claim.

## 4. Feature specifications

### 4.1 User-facing experience

Agent author — assert the span tree with an in-memory sink:

```python
# python
import forgesight as af
from forgesight.testing import InMemoryExporter, assert_span_tree, find_span

def test_agent_records_llm_call():
    sink = InMemoryExporter()
    rt = af.configure(exporters=[sink])           # synchronous, in-memory

    with af.telemetry.agent_run("classifier", version="1.0.0") as run:
        with run.step("react-1"):
            run.llm_call(provider="anthropic", request_model="claude-sonnet-4-5",
                         usage=af.TokenUsage(input=120, output=30))

    rt.force_flush()                              # deterministic drain — no sleeps

    assert_span_tree(sink, {
        "op": "invoke_agent", "name": "invoke_agent classifier",
        "children": [
            {"op": "plan", "children": [
                {"op": "chat", "attrs": {
                    "gen_ai.provider.name": "anthropic",
                    "gen_ai.usage.input_tokens": 120,
                }},
            ]},
        ],
    })
    chat = find_span(sink, op="chat")
    assert chat.attrs["forgesight.usage.cost_usd"] is not None
```

Integration author — run the conformance suite against your implementation:

```python
# test_my_clickhouse_exporter.py
from forgesight.testing.conformance import run_exporter_conformance
from my_pkg import ClickHouseExporter

def test_clickhouse_exporter_conformance():
    # The suite drives the exporter through every contract invariant:
    # non-raising export (returns FAILURE, never throws — P6), force_flush/shutdown
    # idempotency, batch handling, post-shutdown no-op, ExportResult semantics.
    run_exporter_conformance(lambda: ClickHouseExporter(dsn="memory://"))
```

```python
# Same shape for the other three SPIs:
from forgesight.testing.conformance import (
    run_interceptor_conformance,      # None-drops, never-raises-into-runtime, idempotent
    run_event_listener_conformance,   # ignores-unknown-kinds, never-raises-into-runtime
    run_pricing_conformance,          # unknown-model→None, deterministic, no-raise
)
run_interceptor_conformance(MyRedactor)
run_event_listener_conformance(MyKafkaListener)
run_pricing_conformance(MyPricingProvider)
```

pytest fixtures (auto-available once the plugin is installed):

```python
def test_with_fixtures(af_sink, af_runtime, llm_call_factory):
    with af.telemetry.agent_run("a"):
        af.telemetry.record(llm_call_factory(input=10, output=5))
    af_runtime.force_flush()
    assert len(af_sink.records) == 2              # the run + the llm call
```

```typescript
// typescript
import { InMemoryExporter, assertSpanTree, findSpan } from '@agentforge/sdk/testing';
import { runExporterConformance } from '@agentforge/sdk/testing/conformance';

test('exporter conformance', () => {
  runExporterConformance(() => new MyExporter());
});
```

### 4.2 Public API / contract

```python
# forgesight/testing/__init__.py — STABLE testing surface

class InMemoryExporter:                           # implements TelemetryExporter
    """Synchronous, in-process sink. Records are appended verbatim (post-interceptor).
    The canonical exporter for tests — no network, no batching delay."""
    records: list[Record]
    spans: list[SpanData]                         # records rendered as a span tree
    def export(self, records: Sequence[Record]) -> ExportResult: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
    def clear(self) -> None: ...

def assert_span_tree(sink: InMemoryExporter, expected: dict) -> None:
    """Assert the recorded span tree matches `expected` (op/name/attrs/children).
    Order-insensitive on siblings by default; raises AssertionError with a diff."""

def find_span(sink: InMemoryExporter, *, op: str | None = None,
              name: str | None = None) -> SpanData: ...      # exactly one match
def find_spans(sink: InMemoryExporter, *, op: str | None = None,
               name: str | None = None) -> list[SpanData]: ...

# Record / usage factories — STABLE
def llm_call_factory(**overrides) -> LLMCall: ...
def tool_call_factory(**overrides) -> ToolCall: ...
def token_usage_factory(**overrides) -> TokenUsage: ...
```

```python
# forgesight/testing/conformance.py — STABLE (P10 / NFR-7)
from typing import Callable

def run_exporter_conformance(factory: Callable[[], TelemetryExporter]) -> None:
    """Run every exporter-contract invariant against a fresh exporter from
    `factory`. Asserts: export never raises (returns FAILURE on internal error,
    P6); force_flush/shutdown are idempotent + honor timeout; export after
    shutdown is a no-op; ExportResult is SUCCESS|FAILURE; a batch of N records is
    accepted whole."""

def run_interceptor_conformance(factory: Callable[[], Interceptor]) -> None:
    """Asserts: intercept never raises into the runtime; returning None drops;
    returning a Record passes it on; idempotent on replay; does not mutate the
    input record in place (returns a value)."""

def run_event_listener_conformance(factory: Callable[[], EventListener]) -> None:
    """Asserts: on_event never raises into the runtime; unknown EventKind values
    are ignored gracefully; delivery of the full RUN_STARTED→RUN_COMPLETED
    sequence is handled."""

def run_pricing_conformance(factory: Callable[[], PricingProvider]) -> None:
    """Asserts: price() never raises; unknown (provider, model) returns None (not
    an error, not 0.0); a known model returns a deterministic, non-negative float;
    a zero-token usage returns 0.0 or None, never negative."""
```

```typescript
// @agentforge/sdk/testing — STABLE
export class InMemoryExporter implements TelemetryExporter { /* records, spans, clear() */ }
export function assertSpanTree(sink: InMemoryExporter, expected: object): void;
export function findSpan(sink: InMemoryExporter, q: { op?: string; name?: string }): SpanData;
// @agentforge/sdk/testing/conformance
export function runExporterConformance(factory: () => TelemetryExporter): void;
export function runInterceptorConformance(factory: () => Interceptor): void;
export function runEventListenerConformance(factory: () => EventListener): void;
export function runPricingConformance(factory: () => PricingProvider): void;
```

**Stable:** `InMemoryExporter`, `assert_span_tree`, `find_span`/`find_spans`, the
factories, and the four `run_*_conformance` entry points — these are depended on
by every integration's test suite (P5). **Experimental:** the exact `SpanData`
field set and the `assert_span_tree` diff format.

### 4.3 Internal mechanics

**Two consumers, two namespaces.** The lightweight pieces an agent author needs in
unit tests — `InMemoryExporter`, the assertions, the factories — live in the
`forgesight.testing` namespace inside `forgesight-core` (so they're
available wherever the SDK is, no extra dep). The heavier machinery — the
conformance suites, the pytest plugin, and any test-only fixtures — ships as a
separate `forgesight-testing` dev package, so production installs don't carry
test scaffolding (NFR-6 footprint).

**Deterministic flush.** Production export is async + batched (feat-003); tests
need the opposite. `InMemoryExporter` exports synchronously, and the test path
exposes `force_flush()` that **drains the queue and runs the worker inline** so
that by the time it returns, every produced record has been exported. No sleeps,
no polling, no flake — the design-doc open question
([`exporter-pipeline.md`](../design/exporter-pipeline.md) §8.2) is answered here:
the in-memory exporter + a deterministic flush suffice; no separate "inline mode"
is needed.

**Span-tree rendering.** `InMemoryExporter` keeps records verbatim *and* renders
them into a `SpanData` tree using the same parent/child + op-name logic the OTel
exporter uses ([`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md)
§4.2), so `assert_span_tree` checks the *same* shape a real OTLP backend would
see. `find_span(op="chat")` matches on `gen_ai.operation.name`; `find_spans`
returns all matches.

**Conformance suites as contract-as-code (P10).** Each suite is the SPI's prose
contract turned into executable assertions. The pattern: the suite takes a
*factory* (so it gets a fresh instance per case), then drives the implementation
through every invariant the SPI promises and the architecture's failure-modes
table (architecture §8) requires. The decisive ones:

```
run_exporter_conformance:
  • export() raising internally ⇒ caught by the suite as a FAILURE return, never a throw (P6)
  • force_flush()/shutdown() idempotent; honor timeout; return correctly
  • export() after shutdown() is a no-op
  • a batch of N records is consumed whole; partial-failure semantics hold

run_interceptor_conformance:
  • intercept() that raises ⇒ suite asserts the SDK isolates it (chain continues)
  • return None ⇒ record dropped
  • returns a value, doesn't mutate the input in place

run_event_listener_conformance:
  • on_event() that raises ⇒ run + sibling listeners unaffected (feat-007 P6)
  • an unknown EventKind is ignored (open-set forward-compat)

run_pricing_conformance:
  • unknown (provider, model) ⇒ None (not raise, not 0.0) — feat-006 graceful degrade
  • known model ⇒ deterministic, non-negative
```

Every **shipped** implementation (the console/in-memory exporters,
`forgesight-otel`, the `ContentCaptureGate`/`PIIRedactionInterceptor`, the
default `TablePricingProvider`) runs its matching suite in the SDK's own CI; every
**third-party** implementation is *expected* to (the suites are the public bar an
integration meets to call itself conformant). This is the single mechanism that
keeps N integrations honest to one contract (P10).

**pytest plugin.** `forgesight-testing` registers a pytest plugin exposing
fixtures (`af_sink`, `af_runtime`, the factories) and ensures each test gets an
isolated runtime + a cleared sink, so tests don't leak telemetry into each other.

### 4.4 Module packaging

- **`forgesight.testing`** (the agent-author surface: `InMemoryExporter`,
  `assert_span_tree`, `find_span`, factories) lives **inside
  `forgesight-core`** — always present, importable in any project's tests with
  no extra install. It pulls no test framework (pytest is not a core dep).
- **`forgesight-testing`** (the conformance suites + pytest plugin) is a
  **separate dev package** so production installs stay lean (NFR-6):

  ```bash
  pip install --group dev forgesight-testing      # or: uv add --dev forgesight-testing
  ```

  ```toml
  # the pytest plugin auto-registers via its entry point
  [project.entry-points.pytest11]
  forgesight = "forgesight_testing.plugin"
  ```

- **No new SPI entry-point group.** This feature *consumes* the four SPIs to test
  them; it doesn't add a fifth. The `InMemoryExporter` registers under the
  existing `forgesight.exporters` group as `in-memory` (the zero-config
  non-TTY default, feat-010), so it's referenceable from config too.

### 4.5 Configuration

Minimal — the harness is code-driven, not config-driven. The only knob:

```yaml
# forgesight.yaml — for tests, select the in-memory sink + deterministic flush
exporters: [in-memory]
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `exporters: [in-memory]` | `FORGESIGHT_EXPORTERS=in-memory` | — | Selects the synchronous test sink (also the zero-config non-TTY default, feat-010). |

Everything else (`force_flush()`, the assertions, the conformance factories) is
called directly in test code; there are no harness-specific YAML keys.

## 5. Plug-and-play & upgrade story

`forgesight.testing` is in `forgesight-core` — always available, nothing
to add. `forgesight-testing` is added later as a dev dependency the moment a
team writes conformance tests for a custom integration — install + import, no
agent-code change. The pytest plugin auto-registers on install.

Upgrade safety (P5): `InMemoryExporter`, the assertion/finder helpers, the
factories, and the four `run_*_conformance` entry points are stable surface — a
third party's `run_exporter_conformance(...)` test keeps working across 0.x.
Crucially, when a *new* behavioural invariant is added to an SPI in a minor, the
conformance suite gains a case — so an integration that re-runs the suite on
upgrade *learns* it now must satisfy the new invariant. The suite is the
upgrade-safety mechanism for integrations, not just a test.

## 6. Cross-language parity

Identical across Python / TypeScript: the `InMemoryExporter` semantics,
deterministic flush, the span-tree assertion model, and — most importantly — the
*behavioural invariants* each conformance suite asserts (a TS exporter and a
Python exporter must satisfy the same non-raising/flush/shutdown contract).
Allowed to differ: the test-runner integration (pytest plugin vs vitest/jest
helpers), idiomatic naming (`assert_span_tree` ↔ `assertSpanTree`), and fixture
mechanics. Python lands first (0.1); the conformance *contract* is the parity
anchor — TS implementations are measured against the same invariants
(architecture §10). 

## 7. Test strategy

(This feature *is* test tooling; the strategy is meta — testing the harness.)

- **Unit:** `InMemoryExporter` records verbatim; `clear()` resets; span-tree
  rendering matches the OTel exporter's shape; `find_span` raises on zero/multiple
  matches, `find_spans` returns all.
- **Determinism:** `force_flush()` guarantees all produced records are exported on
  return — run 1000× with no flake.
- **The suites test themselves (the headline):** a deliberately *broken* exporter
  (one that raises out of `export`) must make `run_exporter_conformance` **fail** —
  proving the suite catches contract violations, not just passes good actors.
  Likewise a redaction-skipping interceptor, a raising listener, and a
  raise-on-unknown pricing provider must each be caught by their suite.
- **Dogfooding:** every shipped exporter/interceptor/listener/pricing provider in
  the SDK runs its matching suite green in CI (NFR-7).
- **Plugin:** the pytest fixtures isolate runtimes between tests (no telemetry
  leakage across tests).

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| A conformance suite that's too strict rejects valid implementations | Suites assert only documented SPI invariants; each assertion cites the contract clause; new invariants land in minors with notice. |
| A suite that's too lax lets violations through | Meta-tests (§7) require a known-bad implementation to *fail* each suite. |
| `assert_span_tree` brittleness on attribute churn | Match is subset-by-default (assert the attrs you care about); sibling order-insensitive; clear diffs. |
| Third parties don't run the suites | The suites are the *public* bar (P10) + a conformance badge; we can't force it, but adoption is the trust signal platform teams look for. |
| Should conformance run against a live backend too? | No — suites test contract behaviour in-memory; live-backend tests are the integration's own concern (mirrors the vendor-backend `*_live.py` skip pattern). |

## 9. Out of scope

- **A general-purpose mocking framework** for the SDK. The in-memory exporter +
  real runtime is the supported path; mocking the instrumentation API is
  discouraged (you'd test the mock).
- **Load / performance benchmarking.** NFR-1 perf benchmarks live with feat-003,
  not here; this is correctness tooling.
- **Snapshot/golden-file management** beyond the assertion helpers. Teams can layer
  their own snapshot tooling on `sink.spans`.
- **Live-backend conformance.** Suites are in-memory; testing an exporter against a
  real Langfuse/ClickHouse instance is the integration's own (skippable) live
  test.
- **A conformance *certification registry*.** We ship the suites; tracking who
  passed is a community/registry concern (feat-022 territory), not this feature.

## 10. References

- [`requirements.md`](../requirements.md) NFR-7 (every SPI ships a conformance suite), §10.2 (new exporter without modifying core)
- [`architecture.md`](../design/architecture.md) §4 (the four SPIs), §6 (extension points), §8 (failure-modes the suites assert), §10 (parity)
- [`design-principles.md`](../design/design-principles.md) P5 (stable contracts), P10 (conformance over trust)
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.6 (flush/shutdown), §8.2 (in-memory + deterministic flush — answered here)
- [`otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2 (span-tree shape the assertions check)
- feat-001 (the SPIs + `Record` the suites exercise), feat-002 (runtime), feat-003 (pipeline + flush)
- feat-006/007/008 (the pricing / listener / interceptor suites apply to their shipped + third-party implementations)
- feat-010 (`in-memory` exporter as the zero-config non-TTY default)
