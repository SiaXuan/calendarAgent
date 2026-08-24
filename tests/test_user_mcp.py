"""Phase D MCP scaffolding (graphs/user_mcp) — graceful, config-driven.

These cover the no-server / bad-config paths (no real MCP server or network).
Real server loading is exercised on-device once a server is configured.
"""
from graphs import user_mcp
from models.user import MCPServerConfig, UserPreferences


async def test_no_servers_is_noop(clean_stores, monkeypatch):
    monkeypatch.setattr("api.preferences.get_current_prefs", lambda: UserPreferences())
    await user_mcp.load_mcp_tools()
    assert user_mcp.get_mcp_tools() == []


async def test_stdio_without_command_is_skipped(clean_stores, monkeypatch):
    prefs = UserPreferences(
        custom_mcp_servers=[MCPServerConfig(name="broken", transport="stdio")]  # no command
    )
    monkeypatch.setattr("api.preferences.get_current_prefs", lambda: prefs)
    await user_mcp.load_mcp_tools()   # skipped, must not crash the agent
    assert user_mcp.get_mcp_tools() == []


def test_config_defaults():
    c = MCPServerConfig(name="outlook")
    assert c.transport == "stdio"
    assert c.args == [] and c.env == {} and c.tool_allowlist == []
