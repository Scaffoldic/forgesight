# feat-010: Configuration & zero-config bootstrap

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-010 |
| **Title** | Configuration & zero-config bootstrap (`configure()`, env + YAML, entry-point auto-load) |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.1 |
| **Languages** | `both` |
| **Module package(s)** | `forgesight-core`, `forgesight` |
| **Depends on** | feat-001, feat-002, feat-003 |
| **Blocks** | none |

---

## 1. Why this feature

The SDK's headline promise is "instrument any agent in < 10 lines" (requirements
§1.2). That promise dies if step one is a page of wiring — building a pipeline,
constructing exporters, registering interceptors and listeners and a pricing
provider, threading config through. Two concrete pains:

- **The first-run cliff.** A developer who just `pip install`ed the SDK and wants
  to *see something* shouldn't have to pick a backend, stand up a collector, or
  read the pipeline doc. `import forgesight; forgesight.configure()` must
  Just Work and print a span tree to the console.
- **The dev→prod gap.** The same agent code has to run with console export
  locally, an OTLP collector in staging, and OTLP-plus-Langfuse-plus-a-budget-
  interceptor in prod — with **only config changing**, never agent code (success
  criterion §10.4). Without a declarative layer, "swap the backend" becomes a
  code edit and a redeploy, and the dev's laptop config leaks into prod.

And there's a failure that must happen *early*: if prod config names a `langfuse`
exporter but the package isn't installed, the agent must refuse to start at
`configure()` with a clear message — not discover it mid-run when a record can't
be exported and gets silently dropped.

This feature is FR-12: `configure()` that works with zero args, layered
declarative config (`forgesight.yaml` + `FORGESIGHT_*` env + kwargs),
named exporters/interceptors/listeners/pricing resolved via entry points,
Pydantic config models with documented defaults (P8), `${ENV}` interpolation,
and `ExporterNotRegisteredError` fail-fast.

## 2. Why this belongs in the SDK

- **Zero-config is the on-ramp the whole "< 10 lines" claim rests on.** If the
  default bootstrap weren't framework-owned, every agent would hand-build a
  pipeline, and "instrument in < 10 lines" would be false. The sensible default
  (console/in-memory in dev, atexit flush) has to ship in core.
- **One config schema is what makes agents swappable and comparable.** Because
  every agent reads the *same* `FORGESIGHT_*` keys and the *same* YAML
  schema, a platform team can set fleet-wide defaults (the org collector, sample
  rate, redaction policy) once via env, and every agent inherits them without a
  code change. Per-agent config formats would make that impossible — the
  cross-language parity guarantee ([`architecture.md`](../design/architecture.md)
  §10) explicitly includes "config keys" for this reason.
- **Entry-point resolution is the mechanism behind plug-and-play (P2).**
  "`pip install forgesight-langfuse` + one config line" only works if
  `configure()` can resolve the name `langfuse` to an installed exporter via
  entry points. That resolver — and its fail-fast when the name is unknown —
  belongs in core; it's the seam every integration plugs into.
- **Fail-fast is a safety invariant.** `ExporterNotRegisteredError` at
  `configure()` (architecture §8) prevents the worst failure mode: a
  misconfigured backend discovered *mid-run* when telemetry silently drops. The
  bootstrap is the only place that can check the whole config before a single
  record is produced.
- **The anti-pattern if we leave it out:** every agent invents its own config
  format and bootstrap, platform teams can't standardise, dev config leaks to
  prod, and a typo'd exporter name fails silently in production instead of loudly
  at startup.

## 3. How consuming agents/teams benefit

- **Before:** to see a trace locally, a dev constructs a `Pipeline`, a
  `ConsoleExporter`, registers it, and remembers an `atexit` flush — ~20 lines
  before the agent does anything. **After:** `forgesight.configure()` — one
  line, console output, flush on exit, done.
- **Before:** going to prod means editing agent code to construct the OTLP
  exporter, the Langfuse exporter, and the redaction interceptor, then
  redeploying the *code*. **After:** the same binary, an `forgesight.yaml`
  (or `FORGESIGHT_EXPORTERS=otel,langfuse`) — config-only, no code change
  (success criterion §10.4).
- **Add/swap a backend in one line.** `pip install forgesight-langfuse`,
  add `langfuse` to the exporters list — the SDK resolves it via its entry point.
  No import, no constructor in agent code (P2).
- **Platform sets fleet defaults once.** The org exports
  `FORGESIGHT_OTLP_ENDPOINT` and `FORGESIGHT_SAMPLE_RATE` in the base
  image; every agent inherits them; an individual agent overrides via its YAML or
  a `configure()` kwarg — clear, layered precedence.
- **Typos fail at startup, not in prod.** A `langfuse` exporter named in config
  without the package installed raises `ExporterNotRegisteredError` *at
  `configure()`*, naming the expected entry-point — caught in CI, never a silent
  mid-run drop.
- **Defaults are documented, not magic.** Every knob (queue size, batch size,
  sample rate, timeouts) is a Pydantic field with a documented default (P8); a dev
  can dump the resolved config to see exactly what's in effect.

## 4. Feature specifications

### 4.1 User-facing experience

Zero-config — works the instant you install:

```python
# python
import forgesight as af

af.configure()        # no args: console exporter in a TTY / in-memory otherwise,
                      # default pricing provider, atexit flush installed.

with af.telemetry.agent_run("hello-agent") as run:
    run.llm_call(provider="anthropic", request_model="claude-sonnet-4-5",
                 usage=af.TokenUsage(input=120, output=30))
# → a span tree + cost prints to the console; flushed on exit.
```

Declarative — same code, prod config in a file and/or env:

```python
af.configure()        # auto-discovers forgesight.yaml + FORGESIGHT_* env
```

```yaml
# forgesight.yaml  (file layer)
service_name: "issue-classifier"
exporters: [otel, langfuse]
sample_rate: 1.0
```

```bash
# env layer overrides the file
export FORGESIGHT_OTLP_ENDPOINT="http://otel-collector:4317"
export FORGESIGHT_SAMPLE_RATE="0.1"
```

```python
# kwargs win over everything (file → env → kwargs, last wins)
af.configure(sample_rate=1.0, exporters=["otel"])
```

Fail-fast when a named integration isn't installed:

```python
af.configure(exporters=["langfuse"])    # langfuse package not installed
# raises ExporterNotRegisteredError:
#   "No exporter registered under name 'langfuse'. Expected an entry point in
#    group 'forgesight.exporters' (did you `pip install forgesight-langfuse`?)"
```

```typescript
// typescript
import * as af from '@agentforge/sdk';

af.configure();                                  // zero-config
af.configure({ exporters: ['otel'], sampleRate: 0.1 });   // kwargs win
```

### 4.2 Public API / contract

```python
# forgesight/__init__.py — STABLE facade
def configure(
    *,
    service_name: str | None = None,
    exporters: list[str | TelemetryExporter] | None = None,
    interceptors: list[str | Interceptor] | None = None,
    listeners: list[str | EventListener] | None = None,
    pricing: str | PricingProvider | None = None,
    capture_content: bool | None = None,
    sample_rate: float | None = None,
    config_file: str | None = None,          # default: search CWD + FORGESIGHT_CONFIG
    **overrides: object,                     # any other Settings field
) -> "SdkRuntime":
    """Idempotent bootstrap. Resolves config (file → env → kwargs, last wins),
    resolves named integrations via entry points, builds the pipeline (feat-003),
    registers interceptors/listeners/pricing, installs atexit flush. Names that
    don't resolve raise ExporterNotRegisteredError (and the analogous
    *NotRegisteredError) at this call, never mid-run."""

def register(group: str, name: str):
    """Decorator: register an in-process implementation under a group
    ('exporters' | 'interceptors' | 'listeners' | 'pricing') so it's resolvable
    by name from config exactly like an entry point."""
```

```python
# forgesight_core/config.py — STABLE keys, Pydantic models (P8)
from pydantic import BaseModel, Field

class BatchConfig(BaseModel):                   # mirrors exporter-pipeline.md §4.8
    max_queue_size: int = Field(2048, ge=1)
    max_export_batch_size: int = Field(512, ge=1)
    schedule_delay_millis: int = Field(5000, ge=0)
    export_timeout_millis: int = Field(30000, ge=0)
    # validator: max_export_batch_size <= max_queue_size

class Settings(BaseModel):
    service_name: str = "agentforge-agent"
    exporters: list[str] = Field(default_factory=lambda: ["console"])
    interceptors: list[str] = Field(default_factory=list)   # content-gate auto-prepended
    listeners: list[str] = Field(default_factory=list)
    pricing: str = "default"
    capture_content: bool = False               # P7
    sample_rate: float = Field(1.0, ge=0.0, le=1.0)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    emit_otel_events: bool = False

# forgesight_api/errors.py — STABLE
class ExporterNotRegisteredError(LookupError): ...
class InterceptorNotRegisteredError(LookupError): ...
class EventListenerNotRegisteredError(LookupError): ...
class PricingProviderNotRegisteredError(LookupError): ...
```

```typescript
// @agentforge/sdk — STABLE
export function configure(opts?: {
  serviceName?: string;
  exporters?: (string | TelemetryExporter)[];
  interceptors?: (string | Interceptor)[];
  listeners?: (string | EventListener)[];
  pricing?: string | PricingProvider;
  captureContent?: boolean;
  sampleRate?: number;
  configFile?: string;
}): SdkRuntime;
export function register(group: 'exporters' | 'interceptors' | 'listeners' | 'pricing', name: string): ClassDecorator;
```

**Stable:** `configure()`, `register()`, the `Settings` *keys* + their defaults,
the `FORGESIGHT_*` env names, the YAML schema, and the four
`*NotRegisteredError` types. **Experimental:** the `SdkRuntime` handle's methods
beyond `force_flush()`/`shutdown()`.

### 4.3 Internal mechanics

**Layered precedence (file → env → kwargs; last wins).** `configure()` builds the
effective `Settings` by merging three layers in order:

```
1. file   ── forgesight.yaml (CWD, or config_file=, or FORGESIGHT_CONFIG path)
2. env    ── FORGESIGHT_* variables override matching file keys
3. kwargs ── configure(...) arguments override everything
        │
        ▼  ${ENV} interpolation resolved in the file layer
   validate via Pydantic (typed, ranged, defaulted — P8)
        │
        ▼
   resolve named integrations via entry points  ──▶ fail-fast if unknown
        │
        ▼
   build pipeline (feat-003) · register interceptors (content-gate first, feat-008)
   · register listeners (feat-007) · set pricing provider (feat-006)
   · install atexit flush
```

Each layer is a partial override at the key level (not a wholesale replace), so
setting `FORGESIGHT_SAMPLE_RATE` doesn't wipe the file's `exporters`. Lists
(`exporters`, `interceptors`, `listeners`) replace wholesale when a later layer
sets them (you can't append a single exporter via env precedence — you restate
the list).

**`${ENV}` interpolation.** String values in the YAML file of the form `${VAR}`
or `${VAR:-default}` are substituted from the environment before validation — so
secrets (a Langfuse key, a Slack webhook) live in env and are *referenced* from
the file, never committed. A missing `${VAR}` with no default fails validation at
`configure()`.

**Entry-point resolution + fail-fast.** Named integrations resolve via these
entry-point groups (one per SPI):

```
forgesight.exporters     → TelemetryExporter   (feat-003/004 + 0.2 backends)
forgesight.interceptors  → Interceptor          (feat-008)
forgesight.listeners     → EventListener        (feat-007)
forgesight.pricing       → PricingProvider      (feat-006)
```

For each name in a list, `configure()` looks it up in its group (entry points +
anything registered via `@register`). A name that resolves to no implementation
raises the matching `*NotRegisteredError` **at `configure()`**, with a message
naming the expected group and the likely `pip install` (architecture §8). This is
the single check that turns "silent mid-run drop" into "loud startup failure."
Built-in `console`, `in-memory`, `content-gate`, `pii-redaction`, and `default`
(pricing) register under these groups too — no privileged path.

**Zero-config default.** With no file, no env, no kwargs: `exporters=["console"]`
when stdout is a TTY (so a dev sees the tree), else `["in-memory"]` (so tests and
non-TTY hosts don't spam stdout); `pricing="default"` (the vendored table,
feat-006); `capture_content=False` (P7); the content gate prepended; atexit flush
installed. Nothing reaches a network.

**Idempotency.** `configure()` is idempotent: a second call with the same config
is a no-op; with different config it reconfigures the runtime (flushing the old
pipeline first). This matters for hosts (FastAPI, test fixtures) that may call it
more than once.

### 4.4 Module packaging

- **Config models + resolver live in `forgesight-core`**; the `configure()` /
  `register()` facade lives in `forgesight` (the batteries-included package
  most users install, per [`architecture.md`](../design/architecture.md) §5). The
  four `*NotRegisteredError` types are in `forgesight-api` (the locked leaf).

  ```bash
  pip install forgesight        # configure() + zero-config bootstrap
  ```

- **The entry-point groups are the plug-and-play seam.** An integration package
  declares its implementation under the relevant group; `configure()` then
  resolves it by name. Example (an exporter package):

  ```toml
  # pyproject.toml of forgesight-langfuse
  [project.entry-points."forgesight.exporters"]
  langfuse = "forgesight_langfuse:LangfuseExporter"
  ```

  The four groups — `forgesight.exporters`, `forgesight.interceptors`,
  `forgesight.listeners`, `forgesight.pricing` — are the **only**
  extension registration surface (architecture §6: there is no fifth way).

### 4.5 Configuration

Full YAML schema sketch (every key has a documented default; P8):

```yaml
# forgesight.yaml — the complete schema
service_name: "issue-classifier"        # default: "agentforge-agent"

# Named integrations — resolved via the forgesight.<group> entry points.
exporters: [otel, langfuse]             # default: [console] (TTY) / [in-memory]
interceptors:                           # default: [] (content-gate auto-prepended)
  - name: pii-redaction
    config:
      redact_keys: ["api_key", "authorization", "ssn"]
listeners:                              # default: []
  - name: slack-oncall
    config:
      webhook_url: "${SLACK_ONCALL_WEBHOOK}"   # ${ENV} interpolation
pricing: default                        # default: "default" (vendored table, feat-006)

capture_content: false                  # P7 / ADR-0007 — default false
sample_rate: 1.0                        # head-based; default 1.0
emit_otel_events: false                 # mirror lifecycle events onto spans (feat-007)

# Async export pipeline knobs (exporter-pipeline.md §4.8) — all named + defaulted.
batch:
  max_queue_size: 2048
  max_export_batch_size: 512            # constraint: <= max_queue_size
  schedule_delay_millis: 5000
  export_timeout_millis: 30000

# Per-exporter config blocks (passed to the exporter's factory).
exporter_config:
  otel:
    endpoint: "${FORGESIGHT_OTLP_ENDPOINT:-http://localhost:4317}"
    protocol: grpc                      # grpc | http/protobuf
  langfuse:
    public_key: "${LANGFUSE_PUBLIC_KEY}"
    secret_key: "${LANGFUSE_SECRET_KEY}"
```

Env-var equivalents (the `FORGESIGHT_*` namespace; scalars + comma lists):

| Env | Maps to | Default |
|---|---|---|
| `FORGESIGHT_CONFIG` | path to the YAML file | search CWD |
| `FORGESIGHT_SERVICE_NAME` | `service_name` | `agentforge-agent` |
| `FORGESIGHT_EXPORTERS` | `exporters` (comma list) | `console`/`in-memory` |
| `FORGESIGHT_CAPTURE_CONTENT` | `capture_content` | `false` |
| `FORGESIGHT_SAMPLE_RATE` | `sample_rate` | `1.0` |
| `FORGESIGHT_OTLP_ENDPOINT` | `exporter_config.otel.endpoint` | `http://localhost:4317` |
| `FORGESIGHT_BSP_MAX_QUEUE_SIZE` | `batch.max_queue_size` | `2048` |
| `FORGESIGHT_BSP_MAX_EXPORT_BATCH_SIZE` | `batch.max_export_batch_size` | `512` |
| `FORGESIGHT_BSP_SCHEDULE_DELAY` | `batch.schedule_delay_millis` | `5000` |
| `FORGESIGHT_BSP_EXPORT_TIMEOUT` | `batch.export_timeout_millis` | `30000` |

Validation (Pydantic): `0.0 ≤ sample_rate ≤ 1.0`; `max_export_batch_size ≤
max_queue_size`; every named integration must resolve (else `*NotRegisteredError`);
`${VAR}` with no value and no default fails; unknown top-level keys are rejected
(typo protection).

## 5. Plug-and-play & upgrade story

`configure()` and the schema are in `forgesight-core`/`forgesight` —
always installed. Adding an integration later is the plug-and-play story this
feature *enables* for every other feature: install the package, add its name to
the relevant list, restart — no agent-code change (P2). No scaffold-time decision
is forced; an agent starts zero-config and grows config over time.

Upgrade safety (P5): the `Settings` keys, their defaults, the `FORGESIGHT_*`
env names, the YAML schema, the four entry-point group names, and the
`*NotRegisteredError` types are locked surface. New keys arrive with safe
defaults (a minor bump); a renamed/removed key is a major bump + ADR. An agent's
`forgesight.yaml` written against 0.1 keeps parsing across all 0.x.

## 6. Cross-language parity

Identical across Python / TypeScript (explicitly including config — architecture
§10): the `FORGESIGHT_*` env names, the YAML schema + defaults, the
file→env→kwargs precedence, `${ENV}` interpolation, the four entry-point group
names, zero-config behaviour, and fail-fast on unknown names. Allowed to differ:
the config-model library (Pydantic vs zod/equivalent), idiomatic kwarg naming
(`sample_rate` ↔ `sampleRate`), and discovery mechanics (Python entry points vs a
package-manifest convention). Python lands first (0.1).

## 7. Test strategy

- **Zero-config:** `configure()` with no file/env/kwargs builds a console
  exporter in a TTY, in-memory otherwise, default pricing, atexit flush — and a
  run prints/records a span tree.
- **Precedence:** a key set in all three layers resolves to the kwargs value;
  file-only and env-only keys resolve correctly; lists replace wholesale.
- **Interpolation:** `${VAR}` substitutes from env; `${VAR:-default}` falls back;
  a missing required `${VAR}` fails at `configure()`.
- **Fail-fast:** naming an unregistered exporter/interceptor/listener/pricing
  raises the matching `*NotRegisteredError` at `configure()`, with the group +
  install hint in the message; never a mid-run drop.
- **Validation:** out-of-range `sample_rate`, `batch_size > queue_size`, and
  unknown top-level keys all fail at `configure()`.
- **Idempotency:** double `configure()` with identical config is a no-op;
  differing config reconfigures (flushing the old pipeline).
- **Entry-point resolution:** a test package registering an exporter under
  `forgesight.exporters` is resolvable by name; `@register` equivalents too.
- **Example:** one agent run unchanged across console / OTLP / OTLP+Langfuse,
  switching backends by config only.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Config drift between dev and prod | Layered precedence + `${ENV}` keeps secrets/endpoints in env; a `dump_config()` shows the resolved effective config. |
| Entry-point discovery is slow at startup | Resolution is one-time at `configure()`, off the hot path; cached. |
| A name collides across two installed packages | First-registered wins with a WARN; documented; rare. |
| Multiple `configure()` calls in a host (FastAPI reload, tests) | Idempotent; reconfigure flushes the old pipeline. |
| YAML in code repos tempts committing secrets | `${ENV}` interpolation is the sanctioned pattern; docs forbid literal secrets in the file. |
| Should env be able to *append* to a file list? | No in 0.1 — lists replace wholesale (predictable precedence); revisit if demand appears. |

## 9. Out of scope

- **A config UI / admin server.** The SDK reads a file + env; managing that file
  is the deployment's concern.
- **Hot-reload of config at runtime.** `configure()` is bootstrap-time; live
  reconfiguration beyond an explicit re-`configure()` is out of scope for 0.1.
- **Secret management.** `${ENV}` references secrets; the SDK does not fetch from
  a vault (do that in the env layer).
- **Remote / centralised config fetch.** No "phone home for config" — config is
  local file + env (a platform team bakes defaults into the image).
- **Validating *integration-specific* config** beyond what the integration's
  factory checks. Core validates core keys; an exporter validates its own
  `exporter_config` block.

## 10. References

- [`requirements.md`](../requirements.md) FR-12, §1.2 (goals), §10.4 (swap backend by config)
- [`architecture.md`](../design/architecture.md) §5 (packaging tiers), §6 (extension points / entry-point groups), §7 (lifecycle / bootstrap), §8 (`ExporterNotRegisteredError`), §10 (config in parity)
- [`design-principles.md`](../design/design-principles.md) P1, P2, P8
- [`exporter-pipeline.md`](../design/exporter-pipeline.md) §4.8 (the batch knobs this feature surfaces)
- ADR-0007 (`capture_content` default), ADR-0002 (three-tier packaging)
- feat-001 (SPIs + error types), feat-002 (runtime built by `configure()`), feat-003 (pipeline built by `configure()`)
- feat-006 (`pricing`), feat-007 (`listeners`), feat-008 (`interceptors` + content gate)
