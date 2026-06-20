# ForgeSight — documentation

Vendor-neutral, OpenTelemetry-first observability & execution-telemetry SDK for AI
agents. Track agent runs, LLM / tool / MCP calls, workflows, metrics, cost, and
events — and export to any backend without vendor lock-in.

## Using ForgeSight

If you just want to instrument an agent and ship its telemetry:

- **[`playbooks/`](./playbooks/)** — task-oriented how-to guides: install → instrument →
  run locally with Docker → ship to a backend → FastAPI / GitHub Actions / governance.
- **[`runbooks/`](./runbooks/)** — per-backend & per-integration reference: config knobs,
  what it emits, how to operate it, troubleshooting (verified against the shipped source).

## Understanding ForgeSight

1. [`requirements.md`](./requirements.md) — *what* the SDK must do and the constraints
   it holds to (functional + non-functional requirements, traceability).
2. [`design/architecture.md`](./design/architecture.md) — *how* it fits together
   (context, domain model, SPIs, packaging, lifecycle, failure modes).
3. [`features/README.md`](./features/README.md) — the feature catalogue (feat-NNN) and
   dependency order.

## Design docs

Cross-cutting designs that span features:

- [`design/design-principles.md`](./design/design-principles.md) — the ten principles
  every feature is checked against (P1 vendor-neutral … P10 conformance).
- [`design/otel-semantic-conventions.md`](./design/otel-semantic-conventions.md) — the
  canonical mapping from the domain model to OTel GenAI spans / metrics / attributes.
- [`design/exporter-pipeline.md`](./design/exporter-pipeline.md) — the async, bounded,
  fault-isolated export pipeline.
- [`design/cost-model.md`](./design/cost-model.md) — token → cost via a pluggable,
  refreshable pricing table.

## Decisions

- [`adr/README.md`](./adr/README.md) — architectural decision records (MADR format).

## Layout

```
docs/
├── README.md                 ← you are here
├── playbooks/                ← setup & how-to guides (start here to use it)
├── runbooks/                 ← per-backend/integration reference
├── requirements.md           ← product + engineering requirements
├── design/
│   ├── architecture.md       ← canonical "how it works"
│   ├── design-principles.md  ← the rules
│   ├── otel-semantic-conventions.md
│   ├── exporter-pipeline.md
│   └── cost-model.md
├── adr/                      ← architectural decisions (0001+)
└── features/                 ← feat-NNN specs + catalogue
```

## Conventions

This project follows the workspace doc conventions (templates at
[`/.claude/templates/`](../../../.claude/templates/), pipeline at
[`/.claude/development-pipeline.md`](../../../.claude/development-pipeline.md)). One
feature = one branch = one PR; branch `<NNN>` must match an existing
`docs/features/feat-NNN-*.md`; specs carry an Implementation-status section once
shipped. See [`../AGENTS.md`](../AGENTS.md).
