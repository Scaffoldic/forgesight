# Design Doc: OpenTelemetry GenAI semantic-convention mapping

## Metadata

| Field | Value |
|---|---|
| **Title** | OTel GenAI semconv mapping — the canonical wire format |
| **Status** | accepted |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Related features** | feat-001, feat-004, feat-005, feat-006, feat-016 |

---

## 1. Context

"OpenTelemetry first" (P4) is only meaningful if there is **one** deterministic
mapping from the SDK's domain model to OTel identifiers, and it tracks the real spec.
The GenAI conventions are evolving fast and currently:

- Live in a **dedicated repo**, `open-telemetry/semantic-conventions-genai` (moved out
  of the main `semantic-conventions` repo; the old `opentelemetry.io/.../gen-ai/`
  pages are marked "moved").
- Are **all at `Development` stability** with **no tagged release**.
- Use **`gen_ai.provider.name`** as the provider discriminator (superseding the older
  `gen_ai.system`).
- Carry agent + tool + workflow + MCP conventions, plus an opt-in content-capture
  model that is mid-migration between span attributes and events.

This doc fixes the mapping the SDK emits, names every identifier, and states how we
insulate callers from spec churn.

## 2. Goals

- A single table from each domain type → span name, span kind, and attributes.
- Exact metric instruments + units + buckets.
- A pinning + versioning strategy so spec churn never breaks callers.

## 3. Non-goals

- Re-deriving the spec (cited inline; authoritative source is the repo).
- Mapping for non-OTel backends — those *derive* from this (P4); see their feature
  docs.

## 4. Proposal

### 4.1 Pinning & isolation

- The mapping lives **only** in `forgesight-otel` (feat-004) and a thin set of
  attribute-name constants in `forgesight-api`.
- We **pin to a specific commit** of `semantic-conventions-genai` (recorded in the
  feat-004 spec) since there is no release.
- The mapping is **versioned** (`semconv_version` resource attribute) so a backend can
  tell which revision produced a span. Re-pinning is a feat-004 change, never a
  caller-visible one (P5).
- Everything emitted is `Development`-stability upstream; we treat the *SDK's* mapping
  surface as stable (callers depend on our domain model, not on raw attribute names).

### 4.2 Span mapping

Span name format follows the spec: `{gen_ai.operation.name} {primary identifier}`.

| Domain type | operation.name | Span name | Span kind |
|---|---|---|---|
| **WorkflowRun** | `invoke_workflow` | `invoke_workflow {workflow.name}` | INTERNAL |
| **AgentRun** (local) | `invoke_agent` | `invoke_agent {agent.name}` | INTERNAL |
| **AgentRun** (remote/hosted) | `invoke_agent` | `invoke_agent {agent.name}` | CLIENT |
| **AgentRun** create (hosted) | `create_agent` | `create_agent {agent.name}` | CLIENT |
| **Step** | `plan` / *(custom)* | `plan {agent.name}` / `{step.name}` | INTERNAL |
| **LLMCall** chat | `chat` | `chat {request.model}` | CLIENT |
| **LLMCall** completion | `text_completion` | `text_completion {request.model}` | CLIENT |
| **LLMCall** embeddings | `embeddings` | `embeddings {request.model}` | CLIENT |
| **ToolCall** | `execute_tool` | `execute_tool {tool.name}` | INTERNAL |
| **MCPCall** (tools/call) | `execute_tool` | `tools/call {tool.name}` | CLIENT |
| **MCPCall** (other) | *(unset)* | `{mcp.method.name}` | CLIENT |

### 4.3 Attribute mapping

**Identity / routing (all spans):**

| Domain field | OTel attribute | Notes |
|---|---|---|
| run_id | `gen_ai.agent.id` *(hosted)* / `forgesight.run.id` | Spec discourages transient instance ids on `agent.id`; we emit our ULID as an extension attr and use `agent.id` only for stable hosted ids. |
| agent_name | `gen_ai.agent.name` | |
| agent_version | `gen_ai.agent.version` | |
| context_id | `gen_ai.conversation.id` | Only when a real conversation/session id exists (spec forbids fabricating). |
| provider | `gen_ai.provider.name` | Canonical. `gen_ai.system` only if `emit_legacy_system` opt-in. |
| metadata.* | span attributes (namespaced) | Business metadata (FR-5). |
| error | `error.type` (+ span status) | Stable attr from main semconv (FR-7). |

**LLMCall:**

| Domain field | OTel attribute |
|---|---|
| request_model | `gen_ai.request.model` |
| response_model | `gen_ai.response.model` |
| usage.input | `gen_ai.usage.input_tokens` (incl. cached, per spec) |
| usage.output | `gen_ai.usage.output_tokens` |
| usage.cache_read | `gen_ai.usage.cache_read.input_tokens` |
| usage.cache_creation | `gen_ai.usage.cache_creation.input_tokens` |
| usage.reasoning | `gen_ai.usage.reasoning.output_tokens` |
| finish_reasons | `gen_ai.response.finish_reasons` |
| params.temperature/max_tokens/top_p/top_k/… | `gen_ai.request.temperature` / `…max_tokens` / `…top_p` / `…top_k` / … |
| response id | `gen_ai.response.id` |
| time-to-first-chunk | `gen_ai.response.time_to_first_chunk` |
| **cost_usd** | **`forgesight.usage.cost_usd`** (extension; OTel defines none — ADR-0005) |

**ToolCall:** `gen_ai.tool.name`, `gen_ai.tool.type` (`function`/`extension`/
`datastore`), `gen_ai.tool.call.id`, `gen_ai.tool.description`. Args/results are
**Opt-In** (`gen_ai.tool.call.arguments` / `…result`), gated by content capture (P7).

**MCPCall:** `mcp.method.name` (`tools/call`, `tools/list`, `prompts/get`, …),
`mcp.session.id`, `mcp.protocol.version`, `mcp.resource.uri`; plus `gen_ai.tool.name`
and `gen_ai.operation.name = execute_tool` on `tools/call`. On error set `error.type`
(`tool_error` when `CallToolResult.isError`). We **do not** double-instrument an MCP
tool call with a separate `execute_tool` span (spec guidance).

**Content (Opt-In, P7):** `gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions` — JSON-string on spans (per the published JSON schemas),
emitted only when `capture_content` is on. Off by default.

### 4.4 Metric mapping

| Domain metric | OTel instrument | Type | Unit | Buckets |
|---|---|---|---|---|
| token usage | `gen_ai.client.token.usage` | Histogram | `{token}` | `[1,4,16,64,256,1024,4096,16384,65536,262144,1048576,4194304,16777216,67108864]` |
| op duration | `gen_ai.client.operation.duration` | Histogram | `s` | `[0.01,0.02,0.04,0.08,0.16,0.32,0.64,1.28,2.56,5.12,10.24,20.48,40.96,81.92]` |
| TTFT | `gen_ai.client.operation.time_to_first_chunk` | Histogram | `s` | as duration |
| workflow duration | `gen_ai.workflow.duration` | Histogram | `s` | `[1,5,10,30,60,120,300,600,1800,3600,7200]` |
| MCP op duration | `mcp.client.operation.duration` | Histogram | `s` | as duration |

Token usage is **filtered by `gen_ai.token.type`** (`input`/`output`/…), not split
into separate instruments. Required attrs on token usage: `gen_ai.operation.name`,
`gen_ai.provider.name`, `gen_ai.token.type`; on duration: `gen_ai.operation.name`,
`gen_ai.provider.name` (+ `error.type` on error). Billing rule: **report billed
tokens** when both billed and consumed counts exist.

The SDK's own product metrics (FR-6: `agent_runs_total`, `agent_failures_total`,
`agent_cost_total`, `agent_duration_ms`, `tool_invocations_total`,
`mcp_invocations_total`) are **derived** from these + the run records (feat-005),
emitted alongside the spec instruments — they are the SDK's value-add, namespaced
`agentforge.*`, clearly outside `gen_ai.*`.

### 4.5 Context propagation

Trace context propagates via **W3C TraceContext** (`traceparent` / `tracestate`)
across process and agent (A2A / MCP) boundaries, so a run that fans out to a peer has
one end-to-end trace. `run_id` rides along as baggage / an extension attribute for
log correlation.

## 5. Alternatives considered

| Option | Why not |
|---|---|
| OpenInference `llm.*` conventions | Vendor-origin (Arize); fragments; loses free OTLP-backend reach. We layer on OTel (P4). |
| Emit cost as `gen_ai.usage.cost` | The spec defines no such attribute; squatting risks a future clash. We namespace it `agentforge.*` (ADR-0005). |
| Pin to the moved `opentelemetry.io` pages | Those are frozen/redirected; the live spec is the new repo. |
| Wait for a stable GenAI release | None exists; waiting blocks the product. Isolate + pin instead. |

## 6. Migration / rollout

When upstream cuts a release or renames an attribute, we re-pin in feat-004, bump
`semconv_version`, keep the previous mapping behind a flag for one minor, and update
this doc's tables. Callers see no change (P5).

## 7. Risks

| Risk | Mitigation |
|---|---|
| Attribute renamed upstream | Single mapping module; versioned; one-minor back-compat flag. |
| Backend reads legacy `gen_ai.system` | `emit_legacy_system` opt-in emits both. |
| Content leaks via messages | Opt-in only (P7); redaction interceptor runs first. |

## 8. Open questions

1. Emit content as span attributes only, or also as the
   `gen_ai.client.inference.operation.details` event? *(leaning: span attributes
   primary, event behind a flag — matches `opentelemetry-util-genai`.)*
2. Map `Step` to `plan` always, or only for planning phases? *(leaning: custom step
   name as INTERNAL span; `plan` only when semantically a plan.)*

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-06-14 | Pin to `semantic-conventions-genai` commit; isolate in feat-004 | No release exists; insulate callers from churn |
| 2026-06-14 | `gen_ai.provider.name` canonical; `gen_ai.system` opt-in | Tracks current spec |
| 2026-06-14 | Cost = `forgesight.usage.cost_usd` extension | OTel defines no cost attr |

## 10. References

- semantic-conventions-genai: <https://github.com/open-telemetry/semantic-conventions-genai>
  (spans, agent-spans, metrics, events, mcp, registry/attributes/gen-ai)
- [`cost-model.md`](./cost-model.md), [`architecture.md`](./architecture.md) §4
- feat-001, feat-004, feat-005, feat-006, feat-016
