# Coding Standards

ForgeSight is a `uv` workspace of small, typed Python packages. Code should read like the
surrounding code: match its naming, comment density, and idioms.

## Language & tooling

- **Python 3.11+** (CI runs 3.11 / 3.12 / 3.13). Use modern typing: `X | None`, `list[X]`,
  `from __future__ import annotations` at the top of every module.
- **ruff** for format + lint (line length 100; rules `E,F,I,UP,B,SIM,C4,RUF,PT`). Run
  `ruff format` then `ruff check --fix`.
- **mypy --strict** must pass on `packages/*/src`. No untyped defs, no implicit `Any` on
  public surfaces.

## The hard architectural rules (full list in `AGENTS.md`)

- **Vendor-neutral core.** No backend or model-provider SDK in `forgesight-api` or
  `forgesight-core` (P1). A vendor dependency lives only in its own integration package.
- **Import vendor SDKs lazily** inside the integration package — at call time, not module
  import — so constructing an exporter never touches the network, and a heavy/optional
  dependency doesn't break collection. Route a genuinely untyped/heavy SDK through a small
  `Any`-typed boundary and mark the live-only lines `# pragma: no cover` (see the ddtrace /
  clickhouse-connect edges as the pattern).
- **`export()` never raises** — return `ExportResult.FAILURE` (P6). Same spirit for
  listeners and best-effort I/O: catch, log, degrade.
- **OTel-first** — map onto the GenAI semantic conventions; the wire format lives in
  `forgesight-otel`'s `semconv` module. Don't scatter attribute names.
- **Secure by default** — content is opt-in; never log secrets (DSNs, API keys, tokens).

## Public surface

- Every package ships `py.typed`. Export the public surface explicitly via `__all__`.
- SPIs are `runtime_checkable` `Protocol`s in `forgesight-api`. A new implementation is a
  plain class with the right methods — no base-class import required.
- Adding an optional field with a safe default to a locked type is a minor bump; removing
  or renaming is major (ADR-0006).

## Style

- Prefer dataclasses (`frozen=True, slots=True` for value types) over dicts for structured
  data. Records are immutable; build a new one with `dataclasses.replace`.
- Small, pure helpers over clever one-liners. Keep the hot path (record build + interceptor
  chain + handoff) free of I/O.
- Comments explain *why*, not *what*. Match the file's existing density — don't over-comment.
- No stray `print()`, debug code, commented-out blocks, or owner-less `TODO`s.

## References

[`AGENTS.md`](../../AGENTS.md) · [`docs/design/`](../../docs/design) ·
[`docs/adr/`](../../docs/adr) · [`.claude/standards/testing.md`](./testing.md)
