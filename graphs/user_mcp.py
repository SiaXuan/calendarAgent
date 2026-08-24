"""User-attached MCP servers (Phase D scaffolding).

Loads tools from the MCP servers configured in `UserPreferences.custom_mcp_servers`
and exposes them for the chat ReAct agent's tool belt (see graphs/agent_run.py).
The daily generation graph is deliberately left tool-free — MCP autonomy lives in
the chat agent, where the situation varies (triage email, decide whether to look
up travel time, etc.).

Design notes:
- **Graceful no-op** when nothing is configured or `langchain-mcp-adapters` isn't
  installed — the agent then behaves exactly as before.
- **Load once, cache.** `MultiServerMCPClient.get_tools()` opens a fresh session
  per tool *call*, so we only enumerate tools at startup and reuse the list.
- **Per-server allowlist** keeps the belt small (MS 365 exposes 300+ tools; a
  full belt bloats the prompt and confuses tool selection).
"""
import logging

_log = logging.getLogger("dayflow")

# Cached LangChain tools loaded from configured MCP servers (empty until loaded).
_mcp_tools: list = []


def get_mcp_tools() -> list:
    """Cached MCP tools for the agent belt. Sync — safe to call per run."""
    return list(_mcp_tools)


def _connection(server) -> dict | None:
    """Map an MCPServerConfig to a langchain-mcp-adapters connection dict."""
    if server.transport == "stdio":
        if not server.command:
            _log.warning("MCP server %r is stdio but has no command; skipping", server.name)
            return None
        return {
            "transport": "stdio",
            "command": server.command,
            "args": list(server.args),
            "env": dict(server.env),
        }
    if not server.url:
        _log.warning("MCP server %r is %s but has no url; skipping", server.name, server.transport)
        return None
    return {"transport": server.transport, "url": server.url, "headers": dict(server.headers)}


async def load_mcp_tools() -> None:
    """(Re)load tools from configured MCP servers into the cache. Called at
    startup; safe to call again to pick up preference changes."""
    global _mcp_tools
    _mcp_tools = []

    from api.preferences import get_current_prefs
    servers = get_current_prefs().custom_mcp_servers
    if not servers:
        return

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        _log.warning(
            "custom_mcp_servers configured but langchain-mcp-adapters not installed "
            "(pip install -r requirements.txt); skipping MCP tools"
        )
        return

    connections: dict[str, dict] = {}
    allow: dict[str, set[str]] = {}
    for s in servers:
        conn = _connection(s)
        if conn is None:
            continue
        connections[s.name] = conn
        if s.tool_allowlist:
            allow[s.name] = set(s.tool_allowlist)
    if not connections:
        return

    try:
        client = MultiServerMCPClient(connections)
        collected: list = []
        for name in connections:
            tools = await client.get_tools(server_name=name)
            if name in allow:
                tools = [t for t in tools if t.name in allow[name]]
            collected.extend(tools)
        _mcp_tools = collected
        _log.info(
            "Loaded %d MCP tool(s) from %d server(s): %s",
            len(collected), len(connections), ", ".join(connections),
        )
    except Exception as exc:
        # A bad server config / unreachable server must not take down the agent.
        _log.warning("MCP tool load failed (%s); agent runs without MCP tools", exc)
        _mcp_tools = []
