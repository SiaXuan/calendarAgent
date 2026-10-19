"""
Phase D tool-belt wiring: a configured MCP server becomes callable agent tools.

The scaffolding in `graphs/user_mcp.py` shipped without ever being pointed at a
real server — the original tests here only covered the no-server and bad-config
paths, and left "real server loading is exercised on-device" as a note. So
nothing checked that the config → load → belt → call chain actually worked.

These drive a real MCP server (a trivial stdio one in tests/fixtures) over a
real stdio session — no mocking of the adapter — while staying offline and
deterministic.
"""
import sys
from pathlib import Path

import pytest

from api.preferences import get_current_prefs
from graphs import user_mcp
from models.user import MCPServerConfig

_SERVER = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


@pytest.fixture
def mcp_servers():
    """Point preferences at the echo server; restore whatever was there after.

    Preferences are process-global, so a test that forgot to restore would leak
    a stdio subprocess config into every later test.
    """
    prefs = get_current_prefs()
    original = prefs.custom_mcp_servers
    saved_tools = user_mcp._mcp_tools

    def configure(*servers: MCPServerConfig):
        prefs.custom_mcp_servers = list(servers)

    yield configure

    prefs.custom_mcp_servers = original
    user_mcp._mcp_tools = saved_tools


def _echo_server(**kw) -> MCPServerConfig:
    return MCPServerConfig(
        name="echo", transport="stdio",
        command=sys.executable, args=[_SERVER], **kw,
    )


async def test_configured_server_lands_on_the_belt(mcp_servers):
    mcp_servers(_echo_server())
    await user_mcp.load_mcp_tools()

    names = {t.name for t in user_mcp.get_mcp_tools()}
    assert {"echo", "add"} <= names


async def test_tool_calls_round_trip_to_the_server(mcp_servers):
    """Loading a tool isn't enough — the agent has to be able to call it."""
    mcp_servers(_echo_server())
    await user_mcp.load_mcp_tools()

    add = next(t for t in user_mcp.get_mcp_tools() if t.name == "add")
    result = await add.ainvoke({"a": 2, "b": 3})
    assert "5" in str(result)


async def test_allowlist_narrows_the_belt(mcp_servers):
    """
    The belt has to stay small. A full MS 365 server exposes 300+ tools, which
    bloats the prompt and degrades tool selection — the allowlist is the defence.
    """
    mcp_servers(_echo_server(tool_allowlist=["echo"]))
    await user_mcp.load_mcp_tools()

    names = {t.name for t in user_mcp.get_mcp_tools()}
    assert names == {"echo"}
    assert "excluded_tool" not in names


async def test_no_servers_configured_is_a_clean_no_op(mcp_servers):
    mcp_servers()
    await user_mcp.load_mcp_tools()
    assert user_mcp.get_mcp_tools() == []


async def test_unreachable_server_does_not_break_the_agent(mcp_servers):
    """
    A broken server config must degrade to "no MCP tools", never take the chat
    agent down with it — the agent's own schedule tools still have to work.
    """
    mcp_servers(MCPServerConfig(
        name="broken", transport="stdio",
        command=sys.executable, args=["-c", "import sys; sys.exit(1)"],
    ))
    await user_mcp.load_mcp_tools()
    assert user_mcp.get_mcp_tools() == []


async def test_stdio_server_without_command_is_skipped(mcp_servers):
    mcp_servers(MCPServerConfig(name="no-command", transport="stdio"))
    await user_mcp.load_mcp_tools()
    assert user_mcp.get_mcp_tools() == []


async def test_http_server_without_url_is_skipped(mcp_servers):
    mcp_servers(MCPServerConfig(name="no-url", transport="streamable_http"))
    await user_mcp.load_mcp_tools()
    assert user_mcp.get_mcp_tools() == []


def test_config_defaults():
    c = MCPServerConfig(name="outlook")
    assert c.transport == "stdio"
    assert c.args == [] and c.env == {} and c.tool_allowlist == []
