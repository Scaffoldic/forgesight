# feat-013: Langfuse exporter

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-013 |
| **Title** | Langfuse exporter — OTLP-native ingest + first-party observation/cost mapping |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | both |
| **Module package(s)** | `forgesight-langfuse` |
| **Depends on** | feat-004, feat-001, feat-002 |
| **Blocks** | none |

---

## 1. Why this feature

Langfuse is the dashboard most LLM teams reach for when they want to *read* what
an agent did — trace tree, prompt/completion, token usage, latency, and cost per
trace — and slice it by user, session, and release. An agent team that wants
their runs in Langfuse hits a fork in the road:

- Langfuse now exposes an **OTLP ingest endpoint** (`/api/public/otel`, HTTP,
  Basic auth). Since the SDK's whole point is that it speaks OTLP (P4), the
  obvious path is to point `forgesight-otel` at that endpoint and be done —
  *no Langfuse-specific code at all*.
- But the OTLP path leaves value on the table. Langfuse has a first-party
  **observation model** (`trace` → `span` / `generation` / `tool` observations)
  and prioritises an **ingested cost** when you send one; it also has first-class
  `user_id`, `session_id`, and `tags` on a trace. Mapping the SDK's records onto
  that model — and feeding it the SDK's *computed* cost (feat-006) — gives a
  richer, more Langfuse-native experience than generic OTLP spans.

So the question for a team is not "do I need a package" — it's "do I want the
zero-code OTLP path, or the richer first-party path." This feature documents both
and ships the first-party path so the choice is a config decision, not a rewrite.

## 2. Why this belongs in the SDK ecosystem (vs each team integrating the backend by hand)

- **The OTLP path is already free — and that's the point.** Because the domain
  model maps deterministically onto the GenAI conventions (`architecture.md` §2,
  the keystone), a team can send to Langfuse with `forgesight-otel` and *no*
  dedicated package. Owning that mapping centrally is what makes "any OTLP backend
  works" true for Langfuse too. A team hand-rolling a Langfuse integration would
  re-derive a span→observation mapping that the SDK already produces correctly.
- **The first-party path needs the SDK's cost, and cost is the SDK's.** Langfuse
  *prioritises ingested cost* over its own table lookup. The SDK already computes
  authoritative cost from tokens × a pluggable, versioned pricing table (feat-006,
  `cost-model.md`) — including cached and reasoning tokens and tiered pricing. The
  package feeds that computed `forgesight.usage.cost_usd` straight into the
  generation observation, so Langfuse shows the *same* cost the SDK's metrics and
  every other backend show. A per-team integration would either re-implement cost
  (re-fragmenting the headline signal) or let Langfuse guess from its own table
  (disagreeing with the SDK).
- **Content gating is a framework invariant, not a per-team toggle.** Prompts and
  completions are captured **only when `capture_content` is on** (P7,
  `otel-semantic-conventions.md` §4.3). The package maps input/output onto the
  Langfuse observation *only* through that gate, after the redaction interceptor
  (feat-008) has run. Left to each team, the content gate is the first thing
  someone forgets, and PII ends up in a dashboard.
- **Foundation invariants.** Both paths implement the `TelemetryExporter`
  Protocol, run on the pipeline worker (never the hot path), are fault-isolated
  (a Langfuse outage is caught, counted, invisible to the agent — P6 / NFR-3),
  and pass the exporter conformance suite (feat-011).

This package exists for the same reason the architecture says first-party
packages exist: "to add value the raw OTLP path can't — native cost models …
vendor APIs" (`architecture.md` §2). It is *not* on the core; it wraps exactly
one vendor SDK (P1).

## 3. How consuming agents/teams benefit

- **Zero-code path, today:** point `forgesight-otel` at
  `https://cloud.langfuse.com/api/public/otel` with Basic auth and traces appear
  in Langfuse — no Langfuse package, no agent-code change.
- **Richer path, one config block:** `pip install forgesight-langfuse`, add
  `langfuse` to the exporters list with `public_key` / `secret_key`. Now LLM
  calls land as **generation** observations with token usage and the SDK's
  **computed cost** pre-ingested, tool calls as **tool** observations, steps as
  **span** observations, and the run's business metadata (`user`, `session`,
  `tags`) lifts to the trace.
- **Cost agrees everywhere.** The number in the Langfuse cost column equals the
  `agent_cost_total` metric (feat-005) and the cost on every other backend,
  because all of them read the same `PricingProvider` (feat-006).
- **Decision deferred.** A team can start on the free OTLP path and switch to the
  first-party package later for the richer view — config only, no rewrite.
- **Fan-out:** Langfuse for LLM-quality review *and* Prometheus for ops *and* the
  org OTLP collector, all from one run (FR-11).

## 4. Feature specifications

### 4.1 User-facing experience

```bash
pip install forgesight-langfuse
```

```python
# python — first-party path (richer)
import forgesight
forgesight.configure()      # resolves "langfuse" from the exporters list

# or explicit
from forgesight_langfuse import LangfuseExporter
forgesight.configure(exporters=[
    LangfuseExporter(public_key="pk-lf-...", secret_key="sk-lf-...",
                     host="https://cloud.langfuse.com"),
])
```

```yaml
# forgesight.yaml — first-party path (preferred)
exporters:
  - name: langfuse
    config:
      public_key: "${LANGFUSE_PUBLIC_KEY}"   # pk-lf-...
      secret_key: "${LANGFUSE_SECRET_KEY}"   # sk-lf-...
      host: "https://cloud.langfuse.com"     # or self-hosted / region URL
```

```yaml
# forgesight.yaml — OTLP-native path (NO langfuse package needed)
exporters:
  - name: otlp                               # from forgesight-otel
    config:
      protocol: "http/protobuf"
      endpoint: "https://cloud.langfuse.com/api/public/otel"
      headers:
        Authorization: "Basic ${LANGFUSE_OTLP_BASIC_AUTH}"   # base64(pk-lf-…:sk-lf-…)
```

```typescript
// typescript — first-party path
import { configure } from '@agentforge/sdk';
import { LangfuseExporter } from '@agentforge/sdk-langfuse';
configure({ exporters: [new LangfuseExporter({ publicKey: 'pk-lf-...', secretKey: 'sk-lf-...', host: 'https://cloud.langfuse.com' })] });
```

**Which path do I use?** (documented in the package README and §4.3)

| Use the **OTLP path** (`forgesight-otel` → `/api/public/otel`) when… | Use the **first-party path** (`forgesight-langfuse`) when… |
|---|---|
| You already run `forgesight-otel` and want Langfuse as one more OTLP target. | You want LLM calls rendered as native **generation** observations with the SDK's cost pre-ingested. |
| You want zero extra dependencies. | You want trace-level `user` / `session` / `tags` from business metadata. |
| Generic span rendering is enough. | You want the richest Langfuse-native view and prompt/completion (with `capture_content`). |

### 4.2 Public API / contract

`LangfuseExporter` implements the locked `TelemetryExporter` Protocol
(`architecture.md` §4.2), registered under the entry point name `langfuse`, and
must pass the exporter conformance suite (feat-011).

```python
# forgesight_langfuse/exporter.py
from collections.abc import Sequence
from forgesight_api import Record, ExportResult, TelemetryExporter

class LangfuseExporter(TelemetryExporter):
    """Maps SDK records → Langfuse observation model; ingests the SDK's computed
    cost (Langfuse prioritises ingested cost). Stable from v0.2."""

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        region: str | None = None,            # convenience: resolves a host if given
        flush_at: int = 512,                  # aligns with pipeline batch size
        flush_interval_millis: int = 5_000,
    ) -> None: ...

    # --- TelemetryExporter Protocol (locked) ---
    def export(self, records: Sequence[Record]) -> ExportResult: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...
```

**Record → Langfuse observation mapping** (the first-party contract):

| SDK record (`Kind`) | Langfuse object | Key fields |
|---|---|---|
| `AgentRun` / `WorkflowRun` | **trace** | `id=run_id`, `name=agent_name`, `user_id` / `session_id` / `tags` from metadata, `metadata`, timing |
| `Step` | **span** observation | nested under the trace; `name=step.name` |
| `LLMCall` | **generation** observation | `model`, `usage` (input/output/cached/reasoning), `cost_details` ← `forgesight.usage.cost_usd`, `input`/`output` (gated), `finish_reason`, latency |
| `ToolCall` | **tool** observation | `name`, `input`/`output` (gated), status, duration |
| `MCPCall` (`tools/call`) | **tool** observation | mapped like a tool call (uniform with native — FR-4) |

**Stability:** class name, constructor keywords, config keys, and the
record→observation mapping are stable from v0.2; new optional kwargs arrive with
defaults (P5).

### 4.3 Internal mechanics

**Two paths, one decision.**

```
            ┌───────────────────────── path A: OTLP-native ─────────────────────────┐
records ──► forgesight-otel ──► OTLP/HTTP ──► https://…/api/public/otel ──► Langfuse
            (feat-004; generic GenAI spans; Basic auth header; no langfuse pkg)
            └───────────────────────────────────────────────────────────────────────┘

            ┌──────────────────── path B: first-party (this package) ───────────────┐
records ──► LangfuseExporter.export() ──► map to trace/generation/tool/span ──► Langfuse SDK
            cost_details ← forgesight.usage.cost_usd      input/output gated by capture_content
            └───────────────────────────────────────────────────────────────────────┘
```

**Path A (OTLP-native).** Langfuse's `/api/public/otel` accepts OTLP/HTTP with
`Authorization: Basic base64("<public_key>:<secret_key>")`. The SDK's GenAI spans
(feat-004) render directly; Langfuse maps `gen_ai.*` onto its observation model on
its side and reads the SDK's `forgesight.usage.cost_usd` extension attribute as
ingested cost. This path needs **no langfuse package** — it is the keystone
(`architecture.md` §2) applied to Langfuse. The package's job is to document it.

**Path B (first-party).** `export()` translates each record into the Langfuse SDK
calls above. The crucial behaviours:

- **Cost is ingested, not re-derived.** Langfuse prioritises a cost you send over
  its own model lookup. The exporter sets `cost_details` from the SDK's computed
  `forgesight.usage.cost_usd` (feat-006), so the dashboard cost equals the SDK's
  cost everywhere (no double truth).
- **Content is gated.** `input` (prompt) and `output` (completion) on generation
  observations, and tool args/results, are attached **only when
  `capture_content` is on** (P7), and only after the redaction interceptor
  (feat-008) has scrubbed the record. Off by default; structure/usage/cost still
  flow.
- **Trace-level enrichment.** `user_id`, `session_id`, and `tags` are lifted from
  the run's business metadata (FR-5) onto the trace so Langfuse's user/session
  filters work.
- **Batching aligns with the pipeline.** `export()` is called by the pipeline
  worker with a batch; the exporter hands it to the Langfuse SDK's buffer
  (`flush_at` / `flush_interval`) and `force_flush` / `shutdown` drain it. The
  exporter never blocks the hot path (it runs on the worker — `exporter-pipeline.md`
  §4.3) and a Langfuse outage is caught + counted, never raised (P6).

### 4.4 Module packaging

An **integration package — one backend, one vendor SDK** (`architecture.md` §5),
wrapping exactly **one** vendor SDK: `langfuse`. Per P1 this dependency is
**never** added to `forgesight-core`; it lives only here.

| Package | Provides | Deps |
|---|---|---|
| `forgesight-langfuse` | `LangfuseExporter` (first-party observation/cost mapping) + docs for the OTLP-native path | `forgesight-core`, `langfuse` |

```toml
# forgesight_langfuse/pyproject.toml
[project]
dependencies = ["forgesight-core>=0.2", "langfuse>=2"]

[project.entry-points."forgesight.exporters"]
langfuse = "forgesight_langfuse.exporter:LangfuseExporter"
```

The OTLP-native path needs **only** `forgesight-otel` — no entry in this
package's deps. Installing this package makes `langfuse` resolvable by name from
config (`architecture.md` §6, path 1). No core change.

### 4.5 Configuration

`exporters[].config` + `FORGESIGHT_*` env; constructor kwargs win (FR-12).
Named + defaulted knobs (P8).

| Key | Env | Default | Validation |
|---|---|---|---|
| `public_key` | `FORGESIGHT_LANGFUSE_PUBLIC_KEY` | — (required) | `pk-lf-` prefix |
| `secret_key` | `FORGESIGHT_LANGFUSE_SECRET_KEY` | — (required) | `sk-lf-` prefix; never logged |
| `host` | `FORGESIGHT_LANGFUSE_HOST` | `https://cloud.langfuse.com` | URL |
| `region` | `FORGESIGHT_LANGFUSE_REGION` | `null` | `us` / `eu` → resolves `host` if `host` unset |
| `flush_at` | `FORGESIGHT_LANGFUSE_FLUSH_AT` | `512` | ≥ 1; default tracks pipeline batch size |
| `flush_interval_millis` | `FORGESIGHT_LANGFUSE_FLUSH_INTERVAL` | `5000` | ≥ 0 |

Validation: missing `public_key`/`secret_key` → `ExporterNotRegisteredError`-style
fail-fast at `configure()` (`architecture.md` §8), never mid-run. `region` and an
explicit `host` are mutually exclusive (explicit `host` wins; WARN if both set).
Content capture is **not** configured here — it is the SDK-wide `capture_content`
gate (P7), honoured by this exporter.

## 5. Plug-and-play & upgrade story

Two adoption ladders. **OTLP-native:** already covered if you run
`forgesight-otel` — add the endpoint + Basic auth header, no new package.
**First-party:** `pip install forgesight-langfuse` + the `exporters` block,
no agent-code change (P2); remove by dropping the package + config. A team can
migrate A→B (or run both during a transition) with config only. Class name,
config keys, and the record→observation mapping are stable from v0.2; new knobs
arrive as optional defaults (P5). The `langfuse` SDK is pinned in this package, so
a Langfuse SDK bump never touches callers.

## 6. Cross-language parity

Identical across Python / TypeScript: both paths, the record→observation mapping,
the cost-ingestion rule (ingested cost wins), the content gate, and config keys
(`architecture.md` §10). Allowed to differ: the vendor SDK (`langfuse` Python vs
`langfuse-js`), async idioms, and naming (`public_key`/`publicKey`). TypeScript
targets parity by 0.4.

## 7. Test strategy

- **Unit:** record→observation mapping (run→trace, LLM→generation, tool/MCP→tool,
  step→span); `cost_details` populated from `forgesight.usage.cost_usd`;
  `user`/`session`/`tags` lifted from metadata; **content omitted when
  `capture_content` is off**, present when on (after redaction).
- **Conformance (feat-011):** exporter conformance suite — non-raising `export`,
  idempotent `force_flush`/`shutdown`, fault isolation (Langfuse down ⇒ counted,
  not raised).
- **Integration:** against a Langfuse test project (skips on missing creds) — a
  full run produces a trace with the expected nested observations and the SDK's
  cost on the generation.
- **OTLP-path doc test:** assert the documented header is
  `base64(pk:sk)` and the endpoint is `/api/public/otel`.
- **Example agent:** one run exported to Langfuse first-party; assert cost in the
  dashboard equals `agent_cost_total`.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Two cost numbers (SDK vs Langfuse table) | Ingest the SDK's cost; Langfuse prioritises ingested cost (§4.3). One truth. |
| Prompt/PII leaking to the dashboard | Content gated by `capture_content` (P7) + redaction interceptor (feat-008) runs first. |
| Users unsure which path to pick | Decision table in §4.1 + package README. |
| Langfuse SDK churn | Pinned in this package only (P1); a bump never touches callers. |
| `region` vs `host` ambiguity | Explicit `host` wins; WARN if both set (§4.5). |
| Self-hosted Langfuse | `host` is configurable; OTLP path uses `<self-host>/api/public/otel`. |

## 9. Out of scope

- **Langfuse prompt management / datasets / evals UI.** The SDK emits telemetry;
  it does not drive Langfuse's prompt CMS or eval features (requirements §11).
- **Pulling data back from Langfuse.** Export only; the SDK is a client.
- **Re-deriving cost in Langfuse's table.** We ingest the SDK's cost; we don't
  defer to Langfuse's pricing.
- **Capturing content by default.** Off unless `capture_content` (P7).
- **A Langfuse-specific span convention divergent from GenAI.** The OTLP path uses
  the standard GenAI mapping (P4); the first-party path maps to Langfuse's model
  on top of it, never instead of it.

## 10. References

- [`../design/architecture.md`](../design/architecture.md) §2 (keystone / first-party-package rationale), §4.2 (SPI), §5 (packages)
- [`../design/design-principles.md`](../design/design-principles.md) P1, P2, P4, P6, P7, P10
- [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md) §4.2–4.3 (span/content mapping), §4.3 (cost attr)
- [`../design/cost-model.md`](../design/cost-model.md) (computed cost the exporter ingests)
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.3 (worker), §4.6 (flush/shutdown)
- [`../requirements.md`](../requirements.md) FR-3, FR-5, FR-9, FR-11, NFR-3
- feat-004 (OTLP exporter / GenAI mapping), feat-001/002 (model + runtime), feat-006 (cost), feat-008 (interceptors), feat-011 (conformance)
- Prior art: Langfuse OTLP ingest (`/api/public/otel`), Langfuse observation model, LiteLLM cost map
