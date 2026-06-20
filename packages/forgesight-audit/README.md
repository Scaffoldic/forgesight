# forgesight-audit

A **tamper-evident, complete-capture audit trail** for ForgeSight — a governance-grade
projection of the telemetry the SDK already emits ([feat-023](../../docs/features/feat-023-tamper-evident-audit-trail.md)).

It adds the three things ordinary telemetry lacks:

- **Integrity** — every `AuditEvent` is hash-chained (`prev_hash`/`hash`); `verify()` walks
  the chain so deletion, alteration, or reordering is detectable.
- **Complete capture** — it rides the event bus, so it records every run *even when the
  trace was head-sampled out* of the exporters.
- **A compliance query/export surface** — query by principal / team / kind / time, roll up
  cost, and export an auditor bundle (JSONL + a manifest carrying the head hash).

```python
import forgesight
from forgesight_audit import AuditListener, JsonlAuditSink, AuditQuery, verify

sink = JsonlAuditSink("audit/agent-audit.jsonl")
forgesight.configure(sample_rate=0.1, listeners=[AuditListener(sink)])  # 10% traces, 100% audit

# ... run agents as usual; audit events are recorded at source ...

assert verify(sink).intact                       # prove the log wasn't altered
report = sink.query(AuditQuery(principal="clinician-bot"))
print(report.event_count, report.cost_usd_total)
sink.export(AuditQuery(), to="audit/full.bundle") # JSONL + .manifest.json(head_hash)
```

**Drivers:** `jsonl` (default), `sqlite`, `otel` (emit as OTel log records), `siem`
(JSON lines to a syslog/collector). **Wire it** as a listener (above), via
`configure(listeners=["audit"])`, or `forgesight_audit.install({...})` after `configure()`.

It records — it does not enforce. Policy/budget enforcement is `forgesight-governance`.
Apache-2.0.
