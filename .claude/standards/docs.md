# Documentation Standards

Docs are part of the change, not an afterthought. A feature isn't done until its docs are.

## Where things live

- **`docs/requirements.md`** — what the SDK must do (FR/NFR).
- **`docs/design/`** — architecture, design principles (P1–P…), OTel semconv mapping,
  exporter pipeline, cost model.
- **`docs/adr/`** — immutable architecture decisions. A load-bearing decision gets an ADR;
  never silently reverse one — supersede it with a new ADR.
- **`docs/features/feat-NNN-*.md`** — the canonical per-feature spec. Catalogue:
  `docs/features/README.md`.
- **Package `README.md`** — every package has one: what it is, install, a runnable example,
  config table, and any "out of scope" notes.
- **Root `README.md`** — the marketing + quick-start front door. Keep it current with the
  shipped surface (packages, capabilities).
- **`CHANGELOG.md`** — Keep a Changelog format; every shipped feature adds an entry under
  `[Unreleased]`.

## Per-feature doc duties

When you ship a `feat-NNN`:

1. Update the spec's **status** to shipped (in the PR).
2. Add/refresh the package `README.md` with the real, shipped API (no "when this lands…"
   future-tense once it's merged).
3. Add a `CHANGELOG.md` entry.
4. Update `AGENTS.md` only if conventions changed; update `.claude/state/*` always.
5. Sweep forward references: `git grep -n feat-NNN docs/` and fix dangling "future" mentions.

## Style

- Lead with the value, then the API, then the mechanics. Show a runnable snippet early.
- Reference design principles (`P6`, `P1`) and ADRs by id where a choice needs a why.
- Cross-references must resolve — no dangling links. Code references use
  `path:line` form where helpful.
- Match the existing voice: precise, concrete, no marketing fluff inside design docs (save
  that for the root README).

## References

[`AGENTS.md`](../../AGENTS.md) · [`docs/features/README.md`](../../docs/features/README.md)
· [`CHANGELOG.md`](../../CHANGELOG.md)
