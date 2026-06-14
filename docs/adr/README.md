# Architecture Decision Records — ForgeSight

Every load-bearing architectural decision in ForgeSight is captured as an
immutable ADR, using the **MADR (Markdown ADR)** format — Nygard's original template
extended with decision drivers and option-by-option pros/cons (arc42 §9 compatible).

> **Why ADRs.** The SDK will be depended on by AgentForge and third-party agents and
> will outlive its first set of backends. ADRs preserve the *why* of each choice so a
> future contributor can confirm it still holds or supersede it deliberately.

## Format

ADRs are numbered with 4-digit zero-padded ids (`0001`, `0002`, …). Numbers are
**immutable**. An ADR that no longer reflects practice is marked **Superseded by
ADR-NNNN** and stays in place. Template:
[`/.claude/templates/adr.md`](../../../../.claude/templates/adr.md).

## Status legend

| Status | Meaning |
|---|---|
| **Proposed** | Drafted, awaiting acceptance |
| **Accepted** | Active — describes current architecture |
| **Superseded by ADR-NNNN** | Replaced; kept for history |
| **Deprecated** | No longer relevant; not yet superseded |

## Index

| # | Title | Status | Tags |
|---|---|---|---|
| [0001](./0001-opentelemetry-first-canonical-model.md) | OpenTelemetry-first canonical telemetry model | Accepted | architecture, telemetry |
| [0002](./0002-three-tier-vendor-neutral-packaging.md) | Three-tier, vendor-neutral package model | Accepted | architecture, packaging |
| [0003](./0003-async-fault-isolated-export-pipeline.md) | Async, bounded, fault-isolated export pipeline | Accepted | architecture, reliability |
| [0004](./0004-pin-and-isolate-genai-semconv.md) | Pin + isolate + version the GenAI semconv mapping | Accepted | architecture, telemetry, versioning |
| [0005](./0005-cost-as-namespaced-extension.md) | Cost as a namespaced extension + pluggable pricing | Accepted | architecture, cost |
| [0006](./0006-protocol-spi-as-stable-surface.md) | Protocol-based SPIs as the stable surface | Accepted | architecture, contracts |
| [0007](./0007-content-capture-opt-in.md) | Content capture opt-in (secure by default) | Accepted | security, privacy |
| [0008](./0008-python-first-multilanguage-parity.md) | Python-first with multi-language parity roadmap | Accepted | scope, multi-language |
| [0009](./0009-apache-2-license.md) | Apache 2.0 license | Accepted | licensing, governance |

## Process

- New ADR: copy the template, take the next number, fill every section, set
  `Proposed`, open a PR.
- Superseding: write a new ADR explaining the change; set the old one's status to
  `Superseded by ADR-NNNN`; do not delete or edit the old body beyond the status line.

## References

- Nygard, *Documenting Architecture Decisions* (2011)
- MADR v3: <https://adr.github.io/madr/>
- arc42 §9: <https://docs.arc42.org/section-9/>
