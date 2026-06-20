"""RAG agent — retrieve → generate, on Bedrock.

Shows the classic two-phase RAG shape as nested steps: a ``retrieve`` step (a tool call into
a stand-in vector store) feeding a ``generate`` step (the LLM call), with cost on the
generation. Run: ``uv run --no-sync python -m examples.agents.rag_agent``.
"""

from __future__ import annotations

from forgesight import telemetry

from . import _demo

# A tiny stand-in "vector store" — keyed phrases → document chunks.
_CORPUS = {
    "vendor-neutral": "ForgeSight's core depends on no backend or model-provider SDK; "
    "backends are packages you select by config.",
    "opentelemetry": "ForgeSight maps onto the OpenTelemetry GenAI semantic conventions, so "
    "any OTLP backend works with no dedicated package.",
    "cost": "Token usage is converted to USD via a pluggable pricing table — the same number "
    "everywhere.",
}


def retrieve(query: str, k: int = 2) -> list[str]:
    """Return the k most 'relevant' chunks (naive keyword overlap for the demo)."""
    scored = sorted(
        _CORPUS.items(),
        key=lambda kv: sum(word in kv[0] for word in query.lower().split()),
        reverse=True,
    )
    return [chunk for _, chunk in scored[:k]]


def main() -> None:
    sink = _demo.configure("rag-agent", "/tmp/forgesight-rag-audit.jsonl")
    client = _demo.bedrock_client()
    question = "Is ForgeSight vendor-neutral, and how does it handle cost?"

    print("→ RAG agent on", _demo.MODEL)
    with telemetry.agent_run("rag-agent", version="1.0.0", metadata=_demo.run_metadata()) as run:
        with run.step("retrieve"), run.tool_call("vector_search"):
            chunks = retrieve("vendor-neutral cost")
        with run.step("generate"):
            context = "\n".join(f"- {c}" for c in chunks)
            with run.llm_call("aws.bedrock", _demo.MODEL) as call:
                answer, usage = _demo.chat(
                    client,
                    f"Context:\n{context}\n\nUsing only the context, answer: {question}",
                    system="You are a precise assistant. Cite only the provided context.",
                    max_tokens=160,
                )
                _demo.record(call, usage)

    print(f"  retrieved {len(chunks)} chunks")
    print("  answer:", answer)
    _demo.report("rag-agent", sink)


if __name__ == "__main__":
    main()
