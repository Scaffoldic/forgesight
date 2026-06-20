# Tamper-evident audit trail runbook

> A governance-grade projection of the telemetry the SDK already emits — hash-chained,
> complete-capture, with a compliance query/export surface. **Extra:**
> `pip install "forgesight[audit]"` · **Spec:** [feat-023](../features/feat-023-tamper-evident-audit-trail.md)

## What it does

Adds a second projection alongside the exporters: an append-only, **hash-chained** audit
record with three things ordinary telemetry lacks — **integrity** (`verify()` walks the
`prev_hash`/`hash` chain and pinpoints the first altered/deleted/reordered event),
**complete capture** (it rides the event bus, so it records every run *even when the trace
was head-sampled out* of the exporters), and a **compliance query/export** surface. It
**records**, it does not enforce — enforcement is `forgesight-governance`.

## When to use it

- A regulated agent (PII, finance, healthcare) where you must *prove* what ran and that the
  log wasn't altered.
- Incident review needs the complete, attributed sequence — not a 1-in-N sampled trace.
- You need an auditor bundle (events + cost, integrity-checkable) on demand.
- **Not** for general dashboards (use the exporters) and **not** for enforcement (governance).

## Install

```bash
pip install "forgesight[audit]"   # or: pip install forgesight-audit
```

## Configure

Wire it as a listener — three equivalent ways (no agent-code change):

```python
import forgesight
from forgesight_audit import AuditListener, JsonlAuditSink

# explicit instance
forgesight.configure(
    sample_rate=0.1,                                       # 10% of traces for observability…
    listeners=[AuditListener(JsonlAuditSink("audit/agent-audit.jsonl"))],  # …100% of audit events
)
```

```python
# by name (entry point) or the module install() convention
forgesight.configure(listeners=[{"name": "audit", "config": {"sink": "jsonl", "path": "audit/a.jsonl"}}])
# or, after configure():  forgesight_audit.install({"sink": "jsonl", "path": "audit/a.jsonl"})
```

```yaml
# forgesight.yaml
audit:
  enabled: true
  sink: "jsonl"            # jsonl (default) | sqlite | otel | siem | <custom>
  path: "audit/agent-audit.jsonl"
  redact: true            # write the post-interceptor (redacted) record (P7)
  hash_algorithm: "sha256"
```

## What it records

A stable `AuditKind` taxonomy, each event attributed (principal/owner/team) and cost-stamped:
`run.start`, `run.end`, `model.call`, `tool.call`, `error`, and — when `forgesight-governance`
is installed — `policy.decision` / `budget.event`. Drivers:

| Sink | Backing | Use |
|---|---|---|
| `jsonl` (default) | append-only, hash-chained file | the simplest durable log |
| `sqlite` | hash-chained rows, indexed | queryable local store |
| `otel` | OTel **log records** | bridge into your existing observability backend (P4) |
| `siem` | JSON lines via a transport | ship to a SIEM/syslog collector |

## Operate it

```python
from forgesight_audit import AuditQuery, JsonlAuditSink, verify

sink = JsonlAuditSink("audit/agent-audit.jsonl")
result = verify(sink)                          # prove integrity
assert result.intact, f"chain broke at seq {result.broken_at}: {result.reason}"

report = sink.query(AuditQuery(principal="clinician-bot", since=MARCH_1, until=APRIL_1))
print(report.event_count, report.cost_usd_total)        # attributed cost rollup
sink.export(AuditQuery(), to="audit/clinician-bot.bundle")  # JSONL + .manifest.json(head_hash)
```

**Verify it for real:** with `sample_rate=0.1`, run an agent whose trace is sampled *out* of
the exporters — the audit log still has its full `run.start … run.end` sequence (complete
capture). The chain `verify()`s intact across process restarts (it resumes the chain).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Audit log empty | listener not wired, or `audit.enabled: false` | pass `listeners=[AuditListener(sink)]` / set `enabled: true` |
| `verify()` not intact | a row was altered/deleted/reordered (the point of the chain) | `broken_at`/`reason` name the first break; restore from backup |
| PII in the log | `redact: false` | keep `redact: true` (default) — the sink writes the post-interceptor record (P7) |
| `policy.decision`/`budget.event` missing | `forgesight-governance` not installed | install it; those kinds only emit when governance is present |
| A sink error | a slow/misconfigured driver | `write()` never raises and never blocks the run (P6) — failures are counted, the run is unaffected |

> v1 is tamper-**evident** (a hash chain detects tampering). Cryptographic signing /
> external anchoring of the head hash is a deliberate later phase; `head_hash()` is exposed
> as the hook for it.

## Reference

- [feat-023 spec](../features/feat-023-tamper-evident-audit-trail.md) · package
  [`packages/forgesight-audit`](../../packages/forgesight-audit)
- Playbooks: [install](../playbooks/01-install.md) · [instrument your agent](../playbooks/02-instrument-your-agent.md) · [run locally with Docker](../playbooks/03-run-locally-with-docker.md)
- Related: [governance](./governance.md) (enforcement — audit records its decisions), [export pipeline](./export-pipeline.md) (sampling), [registry](./registry-chargeback.md) (ownership attribution)
