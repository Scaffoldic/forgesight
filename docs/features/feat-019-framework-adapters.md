# feat-019: Framework adapters

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-019 |
| **Title** | Framework adapters — auto-instrument LangGraph / CrewAI / PydanticAI / OpenAI Agents / AgentForge / Spring AI |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-adapters-langgraph`, `forgesight-adapters-crewai`, `forgesight-adapters-pydanticai`, `forgesight-adapters-openai-agents`, `forgesight-adapters-agentforge`, `forgesight-adapters-spring-ai` |
| **Depends on** | feat-002 |
| **Blocks** | none |

---

## 1. Why this feature

Most agents are not hand-written against the SDK's instrumentation API — they
are built on a framework (LangGraph, CrewAI, PydanticAI, OpenAI Agents,
AgentForge, Spring AI). A developer who has a working LangGraph agent does not
want to refactor it to call `telemetry.agent_run(...)` / `llm_call(...)` by
hand; they want telemetry to *appear*.

Concrete pains today:

- A team has a 600-line LangGraph workflow. Adding SDK telemetry by hand means
  finding every node, every LLM call, every tool call, and wrapping each — a
  large, error-prone edit to working code, repeated on every change.
- CrewAI and PydanticAI each expose telemetry hooks, but they are framework-
  specific and produce framework-specific shapes. A platform team running agents
  on three frameworks gets three incompatible telemetry stories — the exact
  "no two agents are comparable" problem from requirements §1.1.
- Frameworks emit their own callbacks/events already (LangChain callbacks,
  CrewAI event bus, PydanticAI/OpenAI-Agents instrumentation hooks). The data is
  *right there* — it just needs translating into the SDK's domain model.

The SDK already has a clean instrumentation API (feat-002) and a vendor-neutral
model (feat-001). The missing piece is a translation layer that subscribes to
each framework's native hooks and emits SDK calls — with **no change to the
user's agent code**.

## 2. Why this belongs in the SDK (vs each team wiring it by hand)

- **The translation is per-framework and intricate — exactly what you write
  once.** Mapping a framework's `on_chain_start` / `on_llm_end` / tool-finish
  callbacks onto `agent_run` / `step` / `llm_call` / `tool_call`, getting the
  nesting right, pulling token usage out of the framework's response object —
  this is real work that every team on that framework would otherwise redo
  identically. Shipping one adapter per framework means one correct mapping for
  everyone on it.
- **Uniformity across frameworks is the whole product thesis.** The SDK's value
  (requirements §1) is that an agent's telemetry is *the same shape* regardless
  of framework. That only holds if every framework's adapter targets the *same*
  domain model. Per-team adapters guarantee divergence.
- **"No code change to the agent" is a property only a shared adapter can
  guarantee.** The adapter subscribes to native hooks and instruments from the
  outside; the agent author writes `instrument()` once (or installs the package)
  and never touches their graph/crew/agent definition. A hand-rolled wrapper
  inevitably leaks into the agent code.
- **P3 in practice.** Core privileges no framework (P3); adapters are how
  frameworks become first-class *without* coupling. Each adapter is its own
  opt-in package — convenience, not coupling — so core stays clean and a
  framework can be added/upgraded/dropped independently.
- **Anti-pattern if left to teams:** every team writes a partial, drifting
  adapter for their framework; nesting is wrong half the time; token usage is
  missed; nothing is comparable across frameworks; the adapter rots on the next
  framework upgrade.

Each adapter ships as its own package wrapping exactly one framework (P1/P2) and
is **never** added to core.

## 3. How consuming agents/teams benefit

- **Before:** an agent author edits a 600-line LangGraph workflow to wrap every
  node/LLM/tool call by hand — dozens of edits to working code, redone on every
  change. **After:** `pip install forgesight-adapters-langgraph` + one
  `instrument()` call (or zero, with auto-load) — the *unchanged* graph now
  emits a correct span tree with cost and metrics.
- **Switch frameworks, keep your telemetry.** A team that migrates a crew from
  CrewAI to LangGraph swaps one adapter package; their dashboards, cost
  attribution, and alerts keep working because both adapters target the same
  domain model. Zero dashboard rework.
- **Compare agents across frameworks.** A platform team running LangGraph +
  CrewAI + PydanticAI agents gets one comparable `agent_runs_total` /
  `agent_cost_total` view across all three — impossible with per-framework
  telemetry.
- **Defer the framework decision.** An author can build now and add telemetry
  later as a package install, without rewriting — the deferral story
  requirements §1.2 promises.
- **AgentForge stays first-class without privilege.** AgentForge depends on
  `forgesight-api` directly and emits through it, so its `-agentforge`
  adapter is thin (mostly resource/metadata wiring); other frameworks reach the
  same first-class status purely through their adapter.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python — LangGraph, zero changes to the graph
import forgesight
from forgesight_adapters_langgraph import LangGraphAdapter

forgesight.configure()
LangGraphAdapter().instrument()        # subscribe to LangChain/LangGraph callbacks

# Unchanged user code — now fully instrumented:
result = await my_compiled_graph.ainvoke({"task": "review PR #42"})
```

```python
# python — CrewAI
from forgesight_adapters_crewai import CrewAIAdapter
CrewAIAdapter().instrument()           # subscribe to the CrewAI event bus
result = my_crew.kickoff(inputs=...)   # unchanged
```

```python
# Auto-load: with entry-point auto-instrument on (feat-010), even instrument()
# is optional — configure() activates every installed adapter.
forgesight.configure()             # langgraph + crewai adapters auto-instrument
```

```typescript
// typescript (parity sketch)
import { configure } from '@agentforge/sdk';
import { LangGraphAdapter } from '@agentforge/sdk-adapters-langgraph';

configure();
new LangGraphAdapter().instrument();
const result = await myGraph.invoke({ task: '...' });
```

### 4.2 Public API / contract

```python
# forgesight_api/adapter.py — the shared lifecycle contract (locked)
from typing import Protocol, runtime_checkable

@runtime_checkable
class FrameworkAdapter(Protocol):
    """Translates one framework's native hooks into SDK instrumentation calls."""
    name: str                          # "langgraph", "crewai", …

    def instrument(self) -> None:      # subscribe to native hooks; idempotent
        ...
    def uninstrument(self) -> None:    # unsubscribe; idempotent
        ...
    def is_instrumented(self) -> bool:
        ...

# forgesight_core/adapters/base.py — convenience base (optional)
class BaseAdapter:
    """Default instrument()/uninstrument() bookkeeping: idempotency guard,
    registration with the runtime. Subclasses implement _subscribe()/_unsubscribe().
    """
    name: str
    def instrument(self) -> None: ...        # guarded; calls _subscribe()
    def uninstrument(self) -> None: ...       # guarded; calls _unsubscribe()
    def is_instrumented(self) -> bool: ...
    # subclass hooks:
    def _subscribe(self) -> None: ...
    def _unsubscribe(self) -> None: ...
```

```typescript
// @agentforge/sdk-api
export interface FrameworkAdapter {
  readonly name: string;
  instrument(): void;
  uninstrument(): void;
  isInstrumented(): boolean;
}
```

Stability: the `FrameworkAdapter` Protocol + `BaseAdapter` lifecycle
(`instrument` / `uninstrument` / `is_instrumented`, all idempotent) are the
public surface and are **stable** for 0.2. Each concrete adapter class is
experimental in its first release (mapping fidelity may improve).

### 4.3 Internal mechanics

Every adapter follows the same shape — an instrumentor-style lifecycle that
subscribes to the framework's native callbacks/hooks/events and translates each
into a feat-002 instrumentation call. The adapter holds **no** agent state; the
SDK's `TelemetryContext` (contextvars) carries nesting, so a framework callback
just opens/closes the right span on the active context.

```
adapter.instrument()
   │  register native listeners (idempotent — guarded by is_instrumented)
   ▼
framework runs the user's agent (unchanged):

   native: run/graph/crew start   → telemetry.agent_run(...) opens root span
   native: node / step / iteration→ run.step(...)            INTERNAL span
   native: llm start/end          → run.llm_call(provider, model, usage…) CLIENT span
   native: tool start/end         → run.tool_call(name, type…)            execute_tool
   native: error                  → span ERROR + error.type (FR-7)
   native: run/graph/crew end     → close root span ⇒ record → pipeline

adapter.uninstrument()
   │  unregister listeners (idempotent)
```

The mapping targets the *same* domain model (feat-001) and *same* span/attribute
conventions (feat-004) for every framework — that is what makes cross-framework
telemetry comparable. Token usage is pulled from each framework's response
object into `TokenUsage`; cost is then derived by the runtime (feat-006), so
adapters never compute cost themselves.

**No double-instrumentation.** When a framework call is itself an MCP `tools/call`
(feat-016) the adapter defers to the MCP span rather than opening a second
`execute_tool` — the runtime's re-entrancy guard (feat-016 §4.3) enforces it.

**Per-framework notes** (each subsection brief but concrete):

- **`-langgraph`** — subscribes via the LangChain/LangGraph callback handler
  (`on_chain_start/end`, `on_llm_start/end`, `on_tool_start/end`). Graph
  invocation → `agent_run`; each node → `step`; LLM/tool callbacks → leaf spans.
  Token usage from the callback's `LLMResult`.
- **`-crewai`** — subscribes to the CrewAI event bus
  (`CrewKickoff*`, `AgentExecution*`, `ToolUsage*`, `LLMCall*` events). Crew
  kickoff → `workflow_run`; each agent execution → `agent_run`; tasks → `step`.
- **`-pydanticai`** — hooks PydanticAI's run/model-request instrumentation
  surface (it already emits OTel-shaped spans; the adapter maps its events onto
  the SDK model so they pass through interceptors/pricing/exporters uniformly).
- **`-openai-agents`** — subscribes to the OpenAI Agents SDK tracing/run hooks;
  agent run → `agent_run`, model calls → `llm_call`, tool calls → `tool_call`.
- **`-agentforge`** — **thin.** AgentForge depends on `forgesight-api`
  directly and emits through it (architecture §11), so this adapter only wires
  resource attributes / default business metadata and registers AgentForge's
  hook fan-out; it does not re-translate calls AgentForge already makes.
- **`-spring-ai`** — JVM/Spring AI; bridges Spring AI's advisor/observation hooks
  to the SDK model via the Java surface (NFR-5 staging — lands after the Python/
  TS adapters; listed here for the full set).

### 4.4 Module packaging

Each adapter is its **own** package wrapping exactly one framework (P1/P2/P3),
**never** added to core. Each depends on `forgesight-core` + that one
framework as its single framework dependency. The `FrameworkAdapter` Protocol
lives in `-api` (so AgentForge and third parties can implement it); `BaseAdapter`
lives in `-core`. Core gains no framework dependency.

```bash
pip install forgesight-adapters-langgraph     # or -crewai / -pydanticai /
                                                  # -openai-agents / -agentforge / -spring-ai
```

```yaml
# forgesight.yaml
adapters:
  langgraph: { enabled: true }
  crewai:    { enabled: true }
  # pydanticai / openai-agents / agentforge / spring-ai …
```

Entry-point: each adapter registers under `forgesight.adapters` (e.g.
`langgraph = forgesight_adapters_langgraph:LangGraphAdapter`) so `configure()`
discovers and (when `auto_instrument` is on) instruments every installed,
enabled adapter.

### 4.5 Configuration

| Key | Env | Default | Meaning |
|---|---|---|---|
| `adapters.<name>.enabled` | `FORGESIGHT_ADAPTER_<NAME>` | `true` (when installed) | Per-adapter enable flag. Install ≠ active until enabled. |
| `adapters.auto_instrument` | `FORGESIGHT_ADAPTERS_AUTO` | `true` | `instrument()` every enabled adapter at `configure()`, vs. require an explicit `instrument()` call. |
| `adapters.<name>.capture_content` | `FORGESIGHT_ADAPTER_<NAME>_CAPTURE` | `false` | Per-adapter content capture (prompts/args/results). Off by default (P7); inherits global when unset. |

Validation: an enabled adapter whose framework isn't importable warns and is
skipped (never fails `configure()`, P6); `auto_instrument: false` means adapters
are inert until the app calls `instrument()`.

## 5. Plug-and-play & upgrade story

Add a framework later: `pip install forgesight-adapters-<fw>` + enable it —
no agent-code change (P2/P3). Switch frameworks by swapping the adapter package;
dashboards survive because both target the same model. Remove by uninstalling +
disabling. Minor upgrades may improve a concrete adapter's mapping fidelity
(additive attributes) behind defaults; the `FrameworkAdapter` /
`BaseAdapter` lifecycle stays (P5). A team can also ship its own adapter for an
unlisted framework by implementing `FrameworkAdapter` and registering the entry
point — same path as shipped ones (architecture §6, way 2/3).

## 6. Cross-language parity

Identical: the `FrameworkAdapter` lifecycle (`instrument` / `uninstrument` /
`is_instrumented`, idempotent), the target domain model + span/attribute
conventions, the no-double-instrument rule, per-adapter enable + content-gate
config. Differs: which frameworks exist per runtime — `-langgraph` / `-crewai` /
`-pydanticai` / `-openai-agents` / `-agentforge` are Python-first; their TS
equivalents land toward 0.4 parity; `-spring-ai` is JVM-only. Hook subscription
is idiomatic per framework/runtime.

## 7. Test strategy

- **Conformance (the key one):** a shared adapter-conformance suite (feat-011)
  drives each adapter through a canonical scripted agent run and asserts the
  *same* span-tree shape + attribute set across all adapters — this is how
  cross-framework comparability is enforced, not assumed.
- **Unit per adapter:** native hook → SDK call mapping; nesting correctness;
  token usage extraction; error → span ERROR + `error.type`.
- **Idempotency:** double `instrument()` is a no-op; `uninstrument()` fully
  unsubscribes; re-instrument works.
- **No-double-instrument:** an adapter over an MCP-backed tool defers to the MCP
  span (one span, not two).
- **Integration:** a real minimal agent per framework, end-to-end, asserting the
  span tree against the in-memory exporter.
- **`-agentforge` thinness:** assert it does not duplicate spans AgentForge
  already emits via `-api`.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Framework callback APIs churn across versions | Wrap public hooks only; pin tested version ranges; conformance + per-adapter integration catch drift; adapters version independently. |
| Nesting wrong when a framework runs steps concurrently | Rely on `TelemetryContext` (contextvars) bound at hook entry; conformance asserts tree shape under concurrency. |
| Double-instrument with MCP (feat-016) or native SDK calls | Runtime re-entrancy guard; adapters defer to the inner span; documented contract. |
| `-agentforge` overlapping AgentForge's own `-api` emission | Keep it thin — resource/metadata wiring only; test it adds no duplicate spans. |
| Spring AI is JVM, not Python/TS | Listed for the full framework set; lands on the Java parity track (NFR-5), after Python/TS adapters. |
| Token usage absent from a framework's response | Record the call without usage (cost null, degrade gracefully — FR-9); warn once. |

## 9. Out of scope

- **Orchestrating or replacing frameworks.** Adapters *observe* via native
  hooks; they do not drive execution (requirements §11: not a framework-of-
  frameworks).
- **Monkey-patching framework internals** beyond the supported hook/callback
  surface — if a framework exposes no hook for something, that something is not
  captured until it does (architecture §6: no import-hook extension).
- **A dashboard per framework** — emit only; visualisation is the backend's job.
- **Guaranteeing 1:1 fidelity with every framework's own tracer** — the adapter
  targets the SDK's model; framework-native tracing can run alongside.
- **TS/JVM adapters at 0.2** beyond the Python-first set — staged per NFR-5.

## 10. References

- [`../requirements.md`](../requirements.md) — §1.1 (comparability), §1.2 (defer the framework decision), §8 (adapters assumption), FR-1/2/3
- [`../design/design-principles.md`](../design/design-principles.md) — P1, P2, **P3 (framework agnostic)**, P5, P10
- [`../design/architecture.md`](../design/architecture.md) §2 (adapter path), §6 (extension way 3), §11 (relationship to AgentForge)
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2 (span mapping the adapters target)
- feat-001 (`FrameworkAdapter` Protocol lives in `-api`; domain model), feat-002 (instrumentation API the adapters call), feat-004 (semconv mapping), feat-006 (cost derivation), feat-011 (conformance), feat-016 (MCP no-double-instrument)
- Prior art: LangChain callbacks, CrewAI event bus, PydanticAI / OpenAI Agents instrumentation, Spring AI observability
