"""A minimal stdio MCP server, used to prove the Phase D tool-belt wiring works.

The scaffolding in `graphs/user_mcp.py` was written without ever being pointed at
a real server, so nothing verified that a configured server actually turns into
callable tools on the agent's belt. This server is deliberately trivial and
dependency-free (beyond `mcp`, already required) so that check can live in the
offline test suite instead of needing a third-party server and network.

Run standalone: python tests/fixtures/echo_mcp_server.py
"""
# mcp 2.x renamed FastMCP to MCPServer.
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Return the text you were given. Used to prove tool calls round-trip."""
    return f"echo:{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def whoami(account: str, text: str) -> str:
    """Echo with an account argument — stands in for tools like Gmail's, whose
    user_google_email the backend binds in code rather than letting the model pick."""
    return f"{account}:{text}"


@mcp.tool()
def excluded_tool() -> str:
    """Exists only so a test can prove tool_allowlist actually filters."""
    return "should not be on the belt when an allowlist excludes it"


if __name__ == "__main__":
    mcp.run(transport="stdio")
