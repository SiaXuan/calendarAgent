"""User-attached MCP servers (Phase D).

Loads tools from the MCP servers configured in `UserPreferences.custom_mcp_servers`
and exposes them for the chat ReAct agent's tool belt (see graphs/agent_run.py).
The daily generation graph is deliberately left tool-free — MCP autonomy lives in
the chat agent, where the situation varies (triage email, decide whether to look
up an assignment's due date, etc.).

Design notes:
- **Graceful no-op** when nothing is configured or `langchain[mcp]` isn't
  installed — the agent then behaves exactly as before.
- **One adapter per server**, so a single unreachable server costs only its own
  tools instead of emptying the whole belt.
- **Load once, cache.** Tools are enumerated at startup and reused; a tool call
  opens its own session.
- **Per-server allowlist** keeps the belt small (a Canvas or MS 365 server
  exposes 100-300 tools; a full belt bloats the prompt and degrades tool
  selection). Filtering happens here rather than via the server's own tag
  filtering so the rule is the same whatever the server supports.

Migrated 2026-10-19 from `langchain-mcp-adapters`, which LangChain archived on
2026-09-17 ("MCP support has moved into LangChain under the `langchain.mcp`
namespace"). The old package also pins `mcp<2.0`, so it blocks the client
library the current spec needs. `MultiServerMCPClient(dict_of_servers)` became
`MCPAdapter(one_target)`.
"""
import copy
import logging

_log = logging.getLogger("dayflow")

# Cached LangChain tools loaded from configured MCP servers (empty until loaded).
_mcp_tools: list = []


def get_mcp_tools() -> list:
    """Cached MCP tools for the agent belt. Sync — safe to call per run."""
    return list(_mcp_tools)


def _server_entry(server) -> dict | None:
    """Map an MCPServerConfig to one entry of fastmcp's `mcpServers` config."""
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
    # Our config says "streamable_http"; fastmcp spells it "streamable-http".
    transport = "streamable-http" if server.transport == "streamable_http" else server.transport
    return {"transport": transport, "url": server.url, "headers": dict(server.headers)}


def bind_tool_args(tool, bound: dict):
    """Fix some of a tool's arguments in code and hide them from the model.

    For arguments whose value is known to the backend and isn't the model's
    decision — which Google account to read, for instance. The field is removed
    from the schema the model sees and the value is merged into every call,
    overriding anything the model might still send (hiding a field doesn't stop
    a model from emitting it).

    Arguments the tool doesn't take are ignored, so one server-wide binding can
    apply across tools that only sometimes need it.
    """
    names = [n for n in bound if n in (tool.args or {})]
    if not names:
        return tool

    schema = (copy.deepcopy(tool.args_schema) if isinstance(tool.args_schema, dict)
              else tool.args_schema.model_json_schema())
    for n in names:
        schema.get("properties", {}).pop(n, None)
        if n in schema.get("required", []):
            schema["required"] = [r for r in schema["required"] if r != n]

    fixed = {n: bound[n] for n in names}
    original = tool.coroutine

    async def call_with_bound_args(**arguments):
        return await original(**{**arguments, **fixed})

    return tool.model_copy(update={"args_schema": schema, "coroutine": call_with_bound_args})


async def _tools_for(name: str, entry: dict, allowlist: set[str],
                     bound_args: dict | None = None) -> list:
    """Tools from one server, narrowed by its allowlist, with bound args applied."""
    from langchain.mcp import MCPAdapter

    adapter = MCPAdapter({"mcpServers": {name: entry}})
    tools = await adapter.list_tools()
    if allowlist:
        tools = [t for t in tools if t.name in allowlist]
    if bound_args:
        tools = [bind_tool_args(t, bound_args) for t in tools]
    return tools


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
        import langchain.mcp  # noqa: F401
    except ImportError:
        _log.warning(
            "custom_mcp_servers configured but langchain[mcp] not installed "
            "(pip install -r requirements.txt); skipping MCP tools"
        )
        return

    collected: list = []
    loaded: list[str] = []
    for s in servers:
        entry = _server_entry(s)
        if entry is None:
            continue
        try:
            tools = await _tools_for(s.name, entry, set(s.tool_allowlist), s.bound_args)
        except Exception as exc:
            # One bad server must not cost the others their tools, and must
            # never take down the agent — its own schedule tools still work.
            _log.warning("MCP server %r failed to load (%s); skipping it", s.name, exc)
            continue
        collected.extend(tools)
        loaded.append(f"{s.name}({len(tools)})")

    _mcp_tools = collected
    if loaded:
        _log.info("Loaded %d MCP tool(s) from %s", len(collected), ", ".join(loaded))
