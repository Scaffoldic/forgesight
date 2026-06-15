# Pre-feature checklist

Before writing code for a `feat-NNN`, get oriented so the implementation matches the spec
and the repo's conventions.

## Read

- [ ] The feature spec `docs/features/feat-NNN-*.md` end to end — API, mechanics,
      packaging, config, test strategy, out-of-scope.
- [ ] Its **Depends on** features — confirm the surfaces it builds on actually exist
      (read the code, don't assume the spec; surfaces can have shifted).
- [ ] [`AGENTS.md`](../../AGENTS.md) hard rules + the relevant
      [`.claude/standards/`](../standards) files.
- [ ] An existing sibling package as a template (closest in shape: exporter vs
      instrumentation vs module vs adapter).

## Decide

- [ ] **Package boundary** — which package(s) change. A new backend/target = a new
      `forgesight-<name>` package, never a core change (P1/P3).
- [ ] **Vendor dependency weight** — is the wrapped SDK light (hard-dep + test for real) or
      heavy (lazy import, `pragma: no cover` edge, test the mapping with a double)? Check it
      installs cleanly before committing to it.
- [ ] **Core changes** — keep them minimal, generic, and vendor-neutral. If the feature
      needs a hook in core, prefer a small generic seam over a feature-specific one.
- [ ] **Conformance** — which SPI suite the new type must pass.
- [ ] **Genuine scope forks** (e.g. how many sub-packages, a heavy dependency) → surface to
      the user before building, not after.

## Set up

- [ ] Branch from green `main`: `git switch -c feat-NNN main`.
- [ ] `.claude/state/current.md` updated to `in-progress` with the feature id.
- [ ] `.claude/state/log.md` has a start entry.
