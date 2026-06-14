# CLAUDE.md — forgesight

> Claude Code reads both `CLAUDE.md` and `AGENTS.md`. **`AGENTS.md` is the canonical
> source** for this project. This file is a thin pointer so Claude Code's native
> discovery works.

## Reading order on session resume

1. [`../AGENTS.md`](../AGENTS.md) — project rules (canonical)
2. `.claude/state/current.md` — live work snapshot (create when tracking work)
3. `.claude/state/log.md` — milestone history
4. [`../docs/requirements.md`](../docs/requirements.md)
5. [`../docs/design/architecture.md`](../docs/design/architecture.md)
6. [`../docs/features/README.md`](../docs/features/README.md) + the active feat-NNN spec

## What this project is

A vendor-neutral, OpenTelemetry-first telemetry SDK for AI agents (Python pip package,
TS next). See [`../AGENTS.md`](../AGENTS.md) for the full picture and hard rules.

## The non-negotiables (full list in AGENTS.md)

- Vendor-neutral core: no backend/model-provider SDK in `forgesight-api` /
  `-core`.
- OpenTelemetry first: map onto the GenAI semconv; don't invent attributes.
- Non-blocking & fault-tolerant export; `export()` returns failure, never raises.
- Secure by default: content capture is opt-in.
- Stable SPIs; `mypy --strict`; coverage ≥ 90%; every SPI has a conformance suite.

## Workspace context

This is a self-contained project under the `ai-agents` workspace. The per-feature
pipeline lives at [`/.claude/development-pipeline.md`](../../../.claude/development-pipeline.md);
shared doc templates at [`/.claude/templates/`](../../../.claude/templates/). Don't
pull workspace-root or sibling-project rules into this project's work.
