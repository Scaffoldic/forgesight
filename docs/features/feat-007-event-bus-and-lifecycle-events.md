# feat-007: Event bus & lifecycle events

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-007 |
| **Title** | Event bus & lifecycle events (`EventListener` SPI; `RUN_STARTED`…`MCP_EXECUTED`) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core` |
| **Depends on** | feat-001, feat-002 |
| **Blocks** | feat-021 |

---

## 1. Why this feature

Telemetry export answers "what did the agent do, after the fact, in a dashboard."
Teams need a second thing too: **react to a run as it happens**. The first week
a team runs an agent in production, someone asks for one of:

- "Post to Slack when any run fails." (on-call)
- "Stream every `LLM_EXECUTED` to Kafka so the data team can join it against
  our warehouse." (analytics)
- "Write an immutable audit row for every run, for compliance." (governance)
- "Increment a billing counter on `RUN_COMPLETED`." (FinOps)

None of these is an *exporter* — they are not backends and they are not on the
batched export path. They are **side effects keyed on lifecycle moments**.
Without a sanctioned hook, every team grafts these onto whatever surface they
can reach: monkey-patching the runtime, subclassing the agent, polling the
collector, or — worst — doing the Slack POST *inline in the agent loop*, where
a slow webhook now stalls the run and a 500 from Slack fails it.

This feature is the in-process publish/subscribe surface: lifecycle events are
emitted at well-defined points and delivered to registered `EventListener`s,
in order, with each listener isolated so a raising one can never touch the run
or its siblings.

## 2. Why this belongs in the SDK

- **A lifecycle event is a stable contract, not an integration.** `RUN_STARTED`
  / `RUN_COMPLETED` / `RUN_FAILED` / `STEP_STARTED` / `STEP_COMPLETED` /
  `LLM_EXECUTED` / `TOOL_EXECUTED` / `MCP_EXECUTED` are the canonical moments of
  *any* agent run. If the SDK defines them once (FR-8), a Slack listener written
  against the contract works for every agent on the SDK, forever. If each agent
  defines its own "hook," the surface fragments and no listener is portable.
- **Fault isolation is the load-bearing invariant (P6).** The only safe way to
  run third-party side-effect code next to an agent is to guarantee it cannot
  break the agent. That guarantee — catch, log, count, continue — must live in
  the framework. A team cannot be trusted to wrap every listener correctly, and
  one un-wrapped Slack POST that raises on a network blip would otherwise kill
  production runs.
- **Ordered, in-process delivery is the contract `EventListener`s build on.**
  feat-021 (evaluations & human feedback) subscribes to lifecycle events to
  attach eval-result spans and feedback to a run; it can only do that against a
  defined, ordered event stream. The bus is the substrate other features stand on.
- **The anti-pattern if we leave it out:** every team reinvents pub/sub, each
  with different event names, different delivery guarantees, and different (or
  absent) isolation. Comparing "what fires on failure" across two agents becomes
  impossible, and the first slow webhook takes down a run.

## 3. How consuming agents/teams benefit

- **Before:** the on-call team wants a Slack ping on failure. They wrap the
  agent's `run()` in a try/except, POST to Slack inside the `except`, and now a
  Slack outage turns a recoverable agent error into an unhandled exception. ~30
  lines, fragile, copied into every agent.
  **After:** they register one listener; the SDK delivers `RUN_FAILED` and
  isolates the POST. ~8 lines, and a Slack 500 logs a warning and the run is
  untouched.
- **Before:** the data team wants every LLM call in Kafka. They fork the runtime
  to add a callback. Every SDK upgrade is a merge conflict.
  **After:** they implement `EventListener.on_event`, filter for `LLM_EXECUTED`,
  and produce to Kafka. Zero runtime changes; survives every minor bump (P5).
- **Add a listener without touching agent code.** Listeners are configured as a
  named list in `forgesight.yaml` (§4.5) and resolved via entry points, so
  enabling the audit listener in prod is a one-line config change, not a deploy
  of new agent code.
- **Defer the decision.** An agent ships at day 0 with no listeners. On day 40
  compliance asks for an audit trail; the team adds an `audit` listener to the
  config and ships. The agent's own code never changed.
- **One run, many reactions.** Slack (on-call) + Kafka (analytics) + audit
  (compliance) all subscribe to the same stream; each fires in registration
  order, each isolated from the others.

## 4. Feature specifications

### 4.1 User-facing experience

```python
# python
import forgesight as af
from forgesight_api import EventListener, LifecycleEvent, EventKind


class SlackOnFailure:
    """Ping #oncall when a run fails. A raising POST never touches the run."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def on_event(self, event: LifecycleEvent) -> None:
        if event.kind is EventKind.RUN_FAILED:
            requests.post(self._url, json={"text": f"run {event.run_id} failed"})


af.configure(listeners=[SlackOnFailure("https://hooks.slack.com/...")])

with af.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    run.llm_call(provider="anthropic", request_model="claude-sonnet-4-5")
    ...
# RUN_STARTED → STEP_* / LLM_EXECUTED / TOOL_EXECUTED → RUN_COMPLETED|RUN_FAILED
# are delivered to every registered listener, in registration order.
```

Or declaratively (preferred), with listeners resolved via entry points:

```yaml
# forgesight.yaml
listeners:
  - name: slack-oncall
    config:
      webhook_url: "${SLACK_ONCALL_WEBHOOK}"
  - name: kafka-llm-events
    config:
      topic: "agent.llm.executed"
  - name: audit
```

```typescript
// typescript
import * as af from '@agentforge/sdk';
import { EventListener, LifecycleEvent, EventKind } from '@agentforge/sdk-api';

class SlackOnFailure implements EventListener {
  constructor(private url: string) {}
  onEvent(event: LifecycleEvent): void {
    if (event.kind === EventKind.RUN_FAILED) {
      void postToSlack(this.url, `run ${event.runId} failed`);
    }
  }
}

af.configure({ listeners: [new SlackOnFailure('https://hooks.slack.com/...')] });
```

### 4.2 Public API / contract

```python
# forgesight_api/events.py — STABLE (locked surface, P5)
from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    """The lifecycle moments the SDK publishes (FR-8). Open set: new kinds may
    be appended in a minor release; listeners MUST ignore kinds they don't know."""
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
    kind: EventKind
    run_id: str                              # ULID of the owning AgentRun
    timestamp_unix_nanos: int
    trace_id: str
    span_id: str | None = None               # the span this event fired on, if any
    context_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)   # business metadata (FR-5)
    payload: "Record | None" = None          # the record that triggered it (LLMCall/ToolCall/…),
                                             #   honoring content-capture gating (P7)


# forgesight_api/spi.py — STABLE (already declared in feat-001)
from typing import Protocol, runtime_checkable

@runtime_checkable
class EventListener(Protocol):
    """Side-effect subscriber to lifecycle events. Isolated from the run (P6).
    on_event MUST NOT raise into the runtime; if it does, the SDK catches it."""
    def on_event(self, event: LifecycleEvent) -> None: ...
```

```python
# forgesight_core/events.py — EXPERIMENTAL surface (the bus is core-internal;
# callers register via configure() / @register, not by touching the bus directly)
class EventBus:
    def add_listener(self, listener: EventListener) -> None: ...
    def remove_listener(self, listener: EventListener) -> None: ...
    def emit(self, event: LifecycleEvent) -> None:
        """Deliver to every listener in registration order; isolate each."""
```

```typescript
// @agentforge/sdk-api — STABLE
export enum EventKind {
  RUN_STARTED = 'run_started', RUN_COMPLETED = 'run_completed',
  RUN_FAILED = 'run_failed', STEP_STARTED = 'step_started',
  STEP_COMPLETED = 'step_completed', LLM_EXECUTED = 'llm_executed',
  TOOL_EXECUTED = 'tool_executed', MCP_EXECUTED = 'mcp_executed',
}
export interface LifecycleEvent {
  readonly kind: EventKind; readonly runId: string;
  readonly timestampUnixNanos: number; readonly traceId: string;
  readonly spanId?: string; readonly contextId?: string;
  readonly metadata: Record<string, unknown>; readonly payload?: Record | null;
}
export interface EventListener { onEvent(event: LifecycleEvent): void; }
```

**Stable:** `EventKind` (the eight kinds; the *set* is open but each member is
locked), `LifecycleEvent`, `EventListener`. **Experimental:** `EventBus`
internals — callers never depend on them directly.

### 4.3 Internal mechanics

The bus is driven by the instrumentation runtime (feat-002), which already owns
the run/step/leaf lifecycle. Emission points are fixed:

```
telemetry.agent_run(...)        ──▶ emit RUN_STARTED
  └─ run.step(...)              ──▶ emit STEP_STARTED
       ├─ run.llm_call(...)     ──▶ emit LLM_EXECUTED   (on call completion)
       ├─ run.tool_call(...)    ──▶ emit TOOL_EXECUTED
       └─ run.mcp_call(...)     ──▶ emit MCP_EXECUTED
     (step exit)                ──▶ emit STEP_COMPLETED
(run exit, status OK)           ──▶ emit RUN_COMPLETED
(run exit, status ERROR)        ──▶ emit RUN_FAILED      (see feat-009)
```

**Ordered delivery.** `EventBus.emit` iterates listeners in the order they were
registered (config list order, then any added programmatically). Delivery is
synchronous and in-process: a listener sees `RUN_STARTED` before any child
event, and `RUN_COMPLETED`/`RUN_FAILED` last. This is the ordering feat-021
relies on to nest eval results under the run.

**Fault isolation (P6 — the core invariant).** Each `on_event` call is wrapped:

```
for listener in listeners:           # registration order
    try:
        listener.on_event(event)
    except Exception:                # never propagates to the run
        log via "forgesight.events" (throttled, with run_id)
        increment sdk_listener_errors_total{listener=…}
        continue                     # the next listener still runs
```

A raising listener affects **neither the run nor sibling listeners** — it is
skipped for that event and the loop continues. This mirrors the per-exporter and
per-interceptor isolation in [`exporter-pipeline.md`](../design/exporter-pipeline.md)
§4.4.

**Relationship to exporters and interceptors.** The bus is *not* the export
path. Events are a parallel, side-effect channel:
- **Exporters** receive batched, async `Record`s on the worker (feat-003).
- **Listeners** receive `LifecycleEvent`s in-process at the moment they fire.
- The event `payload` carries the same record, already past the interceptor
  chain (feat-008) — so a redacted field is redacted in the event too, and a
  vetoed record fires no event. Content on the payload honors `capture_content`
  (P7): if content capture is off, the payload carries structure (tokens, cost,
  names) but not prompt/completion text.

**Slow listeners.** Listeners run on the caller's task. A listener that does
blocking I/O (a synchronous Slack POST) *does* add latency to the emit point —
this is documented, and the recommended pattern is to enqueue and return (push
to a queue / `create_task`), exactly as the observability runbook advises for
hooks. The SDK isolates *failures*, not *latency*; the listener owns its own
non-blocking strategy.

**Optional emission as OTel events.** When the OTel exporter (feat-004) is
installed and `emit_otel_events` is on, the bus also records each
`LifecycleEvent` as an OpenTelemetry event on the active span (an `add_event`
with the kind as the event name and the metadata/payload as attributes, gated by
content capture). This is opt-in: the default is in-process listener delivery
only, so a team with no OTel backend pays nothing.

### 4.4 Module packaging

- **Lives in `forgesight-core`** (always installed with `forgesight`).
  The `EventBus` and the emission points are part of the runtime; the
  `EventListener` SPI, `LifecycleEvent`, and `EventKind` live in
  `forgesight-api` (the locked leaf, per
  [`architecture.md`](../design/architecture.md) §5). No new install step —
  the bus is there the moment you `configure()`.

  ```bash
  pip install forgesight        # bus + SPI included; no extra
  ```

- **Custom listeners as entry points.** A listener is resolvable by name from
  config when registered under the entry-point group `forgesight.listeners`:

  ```toml
  # pyproject.toml of the package shipping the listener
  [project.entry-points."forgesight.listeners"]
  slack-oncall = "myorg.telemetry.slack:SlackOnFailure"
  kafka-llm-events = "myorg.telemetry.kafka:KafkaLLMListener"
  audit = "myorg.telemetry.audit:AuditListener"
  ```

  Or in-process: `@forgesight.register("listeners", "slack-oncall")`.
  Either way the name is then usable in the `listeners:` config list (§4.5).
  Built-in listeners are referenced the same way — there is no privileged path.

### 4.5 Configuration

```yaml
# forgesight.yaml
events:
  emit_otel_events: false        # also record events on the active OTel span (feat-004)
  deliver_step_events: true      # STEP_STARTED/STEP_COMPLETED can be muted on hot loops

# The enabled listeners, in delivery order. Each `name` resolves via the
# forgesight.listeners entry-point group; `config` is passed to its factory.
listeners:
  - name: slack-oncall
    config:
      webhook_url: "${SLACK_ONCALL_WEBHOOK}"   # ${ENV} interpolation (feat-010)
  - name: kafka-llm-events
    config:
      bootstrap_servers: "kafka:9092"
      topic: "agent.llm.executed"
  - name: audit                                # no config block ⇒ defaults
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `listeners` | — (list, file/kwargs only) | `[]` | Ordered; each name must resolve to an `forgesight.listeners` entry point or an `ExporterNotRegisteredError`-style fail-fast at `configure()` (feat-010). |
| `events.emit_otel_events` | `FORGESIGHT_EMIT_OTEL_EVENTS` | `false` | Mirror events onto the active span. |
| `events.deliver_step_events` | `FORGESIGHT_DELIVER_STEP_EVENTS` | `true` | Suppress `STEP_*` on very hot loops to cut delivery overhead. |

Validation: each `listeners[].name` must resolve at `configure()`; an unknown
name fails fast with the expected entry-point group named (feat-010). An empty
`listeners` list is valid (the bus simply has no subscribers).

## 5. Plug-and-play & upgrade story

The bus is in `forgesight-core` — always installed; nothing to add at
scaffold time. *Listeners* are the plug-and-play unit: add one later by
installing the package that ships it (or registering it in-process) and adding a
line to `listeners:`. No agent-code change (P2).

Upgrade safety (P5): `EventListener.on_event` and `LifecycleEvent` are locked.
`EventKind` is an **open set** — new kinds may be appended in a minor release, so
listeners MUST ignore kinds they don't recognise (the `if event.kind is …`
pattern in §4.1 does this naturally). `LifecycleEvent` may gain optional fields
with safe defaults in a minor; it never removes or renames one without a major
bump + ADR. A listener written against 0.1 keeps working against 0.x.

## 6. Cross-language parity

Identical across Python / TypeScript: the eight `EventKind`s, the
`LifecycleEvent` shape, the `EventListener` contract, registration-order
delivery, fault isolation, and the optional OTel-event mirroring. Allowed to
differ: idiomatic naming (`on_event` ↔ `onEvent`, `run_id` ↔ `runId`), and the
async primitive a listener uses to defer slow work (`asyncio.create_task` vs a
Promise). Python lands first (0.1); TS targets parity per
[`architecture.md`](../design/architecture.md) §10.

## 7. Test strategy

- **Unit:** every emission point fires exactly the expected kind once;
  `RUN_STARTED` precedes all child events and `RUN_COMPLETED`/`RUN_FAILED` is
  last; `deliver_step_events: false` suppresses only `STEP_*`.
- **Ordering:** N registered listeners receive each event in registration order
  (assert via a recording listener).
- **Fault isolation (the headline test):** a listener that raises on every event
  does not affect the run's result and does not stop sibling listeners; the
  error is logged via `forgesight.events` and counted in
  `sdk_listener_errors_total`.
- **Content gating:** with `capture_content` off, `event.payload` carries
  structure but no prompt/completion text (P7); a record vetoed by an
  interceptor (feat-008) fires no event.
- **Conformance:** `run_event_listener_conformance` (feat-011) — every shipped
  and third-party listener runs the same suite (ignores-unknown-kinds,
  never-raises-into-runtime, idempotent-on-replay).
- **Example:** a Slack-on-failure listener and a Kafka `LLM_EXECUTED` listener
  exercised end-to-end with the in-memory exporter.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| A slow synchronous listener adds latency to the emit point | The SDK isolates failures, not latency; document the enqueue-and-return pattern; `deliver_step_events: false` for hot loops. |
| Should delivery be async (off the agent task) like exporters? | No for 0.1 — synchronous in-process ordering is what feat-021 needs; a listener that wants async owns its own offload. Revisit if a measured need appears. |
| Event ordering across nested/parallel runs | Each event carries `run_id` + `trace_id`; ordering is guaranteed *within* a run, not globally — documented. |
| Listener subscribing to specific kinds only | No server-side filter in 0.1; listeners filter in `on_event` (the `if event.kind is …` pattern). A declarative `kinds:` filter is a possible later add. |
| Content leaking via `event.payload` | Payload honors `capture_content` (P7) and is post-interceptor (feat-008). |

## 9. Out of scope

- **Durable / replayable event log.** The bus is in-process and best-effort; it
  is not a message queue. A listener that needs durability produces to Kafka
  itself (that's the use case, not a bus feature).
- **Cross-process event delivery.** Events fire in the emitting process; fan-out
  to other processes is a *listener's* job (e.g. the Kafka listener).
- **Backpressure on listeners.** Listeners run inline; there is no per-listener
  queue (unlike exporters). A listener that can't keep up must offload itself.
- **Ordering guarantees across concurrent runs.** Only within-run ordering is
  guaranteed.
- **Replacing the export path.** Listeners are side effects, not a backend; use a
  `TelemetryExporter` (feat-003/004) to ship records to a telemetry backend.

## 10. References

- [`requirements.md`](../requirements.md) FR-8, P6
- [`architecture.md`](../design/architecture.md) §4 (SPIs), §5 (packaging), §7 (lifecycle), §8 (failure modes)
- [`design-principles.md`](../design/design-principles.md) P5, P6, P7, P10
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.4 (fault isolation pattern)
- feat-001 (the `EventListener` SPI + `Record`), feat-002 (the runtime that emits)
- feat-008 (interceptors — events carry post-interceptor records)
- feat-021 (evaluations & human feedback — the primary downstream consumer; **blocked by** this)
- feat-011 (`run_event_listener_conformance`)
- Prior art: AgentForge `agentforge-py` feat-009 hook fan-out + error isolation
