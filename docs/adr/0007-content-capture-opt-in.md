# ADR-0007: Content capture opt-in (secure by default)

## Metadata

| Field | Value |
|---|---|
| **Number** | 0007 |
| **Title** | Content capture opt-in (secure by default) |
| **Status** | Accepted |
| **Date** | 2026-06-14 |
| **Deciders** | kjoshi |
| **Tags** | security, privacy |

---

## 1. Context and problem statement

Agent telemetry can include prompts, completions, and tool-call
arguments/results — the most sensitive data the SDK ever touches. It routinely
contains PII, secrets, and regulated material, and it flows out to third-party
backends. A telemetry SDK that captures this content by default turns every
adopter into a data-exfiltration risk and a GDPR/HIPAA liability the moment they
`pip install`. At the same time, token counts, timing, cost, and call structure
are non-sensitive and are exactly what most observability use cases need.

How do we capture rich, useful telemetry by default while ensuring that sensitive
message content is never emitted unless the operator has explicitly chosen to
emit it?

## 2. Decision drivers

- **Secure by default (P7).** Content must not leave the process unless
  explicitly opted in; the safe choice must be the default, not a setting users
  must remember to flip.
- **Regulatory posture.** GDPR/HIPAA-conscious deployments need content-off as the
  out-of-the-box behaviour, with capture a deliberate, auditable decision.
- **Ecosystem consistency.** OTel, Logfire, and Traceloop all default content
  capture *off*; matching them meets least-surprise expectations.
- **Usefulness without content.** The default must still deliver real value —
  tokens, timing, cost, and structure — so opting out of content costs little.

## 3. Considered options

1. **Option A — capture content by default.** Prompts/completions/tool args are
   emitted unless the operator disables them.
2. **Option B — opt-out.** Content captured by default with a documented switch to
   turn it off.
3. **Option C — opt-in.** Content captured only when explicitly enabled via
   `capture_content` (mirroring `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`);
   everything non-sensitive captured by default.

## 4. Decision outcome

**Chosen: Option C — opt-in (secure by default).**

Prompt, completion, and tool-argument/result **content is captured only when the
operator explicitly opts in** via the `capture_content` flag, which mirrors OTel's
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. Token counts, timing, cost,
and call structure are captured by default. The content-capture gate is enforced
*before a record ever reaches an exporter*, and PII redaction is a first-class
interceptor that runs first in the chain — so even with capture on, redaction can
strip what should never leave. This is the secure-by-default posture (P7), aligns
with GDPR/HIPAA expectations, and matches the default of OTel, Logfire, and
Traceloop. The opt-in costs adopters little because the default telemetry is
already richly useful. See
[`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md)
§4.3 (content fields are spec "Opt-In").

### Positive consequences

- A fresh install never exfiltrates prompts/completions; the dangerous action is a
  conscious one (P7).
- Aligns with GDPR/HIPAA-conscious deployment defaults and reduces adopter
  liability.
- Matches OTel/Logfire/Traceloop, so behaviour meets least-surprise expectations.
- Default telemetry (tokens, timing, cost, structure) is still highly useful.
- The gate sits before export and composes with the redaction interceptor for
  defence in depth.

### Negative consequences (trade-offs)

- Out of the box, users debugging prompt/response behaviour see no content until
  they opt in — an extra step and a moment of "where are my messages?".
- Two code paths (content present/absent) must be tested and documented.
- Teams that *want* content everywhere must configure it per deployment.

## 5. Pros and cons of the options

### Option A: capture content by default

- + Richest telemetry with zero configuration.
- − Exfiltrates sensitive data on install; fails P7 and GDPR/HIPAA defaults.
- − Diverges from the whole ecosystem's default-off norm.

### Option B: opt-out

- + Content available by default; one switch to disable.
- − Still default-unsafe: the risky state is the default (fails P7).
- − Relies on users remembering to disable before sensitive data leaks.

### Option C: opt-in (chosen)

- + Secure by default; sensitive capture is a deliberate, auditable choice (P7).
- + Matches OTel/Logfire/Traceloop; GDPR/HIPAA-friendly.
- + Composes with redaction interceptor; gate enforced before export.
- − Content absent until opted in; two paths to test/document.

## 6. References

- Related ADRs: ADR-0004 (content fields are spec "Opt-In" in the mapping),
  ADR-0006 (`Interceptor` SPI — the redaction interceptor), ADR-0003 (the gate
  runs before records reach the pipeline's exporters).
- Design docs: [`../design/design-principles.md`](../design/design-principles.md)
  (P7), [`../design/otel-semantic-conventions.md`](../design/otel-semantic-conventions.md)
  §4.3, [`../design/architecture.md`](../design/architecture.md) §8.
- Prior art: OTel `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`; Logfire and
  Traceloop default-off content capture.
