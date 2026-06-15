"""MCP method ↔ span mapping (otel-semantic-conventions §4.2).

A ``tools/call`` is *both* an MCP method (``mcp.method.name = tools/call``) and a tool
execution (``gen_ai.operation.name = execute_tool``, ``gen_ai.tool.name = <tool>``) — the
SDK's ``MCPScope`` already emits that single span (no double-instrument). This module owns
the small, stable facts the instrumentation needs: which methods are known, and how to pull
the tool / resource / prompt name off each call.
"""

from __future__ import annotations

from collections.abc import Sequence

# Methods the client/server instrumentation spans by default.
TOOLS_CALL = "tools/call"
TOOLS_LIST = "tools/list"
PROMPTS_GET = "prompts/get"
PROMPTS_LIST = "prompts/list"
RESOURCES_READ = "resources/read"
RESOURCES_LIST = "resources/list"

KNOWN_METHODS: frozenset[str] = frozenset(
    {TOOLS_CALL, TOOLS_LIST, PROMPTS_GET, PROMPTS_LIST, RESOURCES_READ, RESOURCES_LIST}
)

# Extra span metadata keys (mapped onto the record's attributes).
MCP_RESOURCE_URI = "mcp.resource.uri"
MCP_PROMPT_NAME = "mcp.prompt.name"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"

# Server-side: low-level MCP request type name → method string. Type *names* (not the
# imported classes) keep this resilient to mcp-SDK import churn.
REQUEST_TYPE_TO_METHOD: dict[str, str] = {
    "CallToolRequest": TOOLS_CALL,
    "ListToolsRequest": TOOLS_LIST,
    "GetPromptRequest": PROMPTS_GET,
    "ListPromptsRequest": PROMPTS_LIST,
    "ReadResourceRequest": RESOURCES_READ,
    "ListResourcesRequest": RESOURCES_LIST,
}


def resolve_methods(methods: Sequence[str] | None) -> frozenset[str]:
    """Resolve a caller's ``methods`` option to a known-method set.

    ``None`` ⇒ all known methods. Unknown names are dropped (forward-compat with new MCP
    methods); the caller is responsible for logging the drop.
    """
    if methods is None:
        return KNOWN_METHODS
    requested = {str(m) for m in methods}
    return frozenset(requested & KNOWN_METHODS)


def unknown_methods(methods: Sequence[str] | None) -> list[str]:
    """Return requested method names that are not known (for a one-time WARN)."""
    if methods is None:
        return []
    return sorted({str(m) for m in methods} - KNOWN_METHODS)
