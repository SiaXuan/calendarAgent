from typing import Literal

from pydantic import BaseModel, Field
from enum import Enum


class Language(str, Enum):
    en = "en"
    zh_CN = "zh-CN"
    zh_TW = "zh-TW"
    ja = "ja"


class MCPServerConfig(BaseModel):
    """A user-attached MCP server (Phase D). The chat agent loads its tools into
    the ReAct tool belt. `tool_allowlist` keeps the belt small — important for
    large servers (e.g. MS 365 has 300+ tools); empty means load all."""
    name: str
    transport: Literal["stdio", "streamable_http", "sse"] = "stdio"
    # stdio transport (a local command, e.g. an npx-launched MCP server)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http/sse transport (a hosted MCP server)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    tool_allowlist: list[str] = Field(default_factory=list)


class UserPreferences(BaseModel):
    language: Language = Language.zh_CN
    work_start: int = Field(default=8, ge=0, le=23)
    work_end: int = Field(default=22, ge=1, le=24)
    max_deep_work_minutes: int = Field(default=90, ge=15, le=180)
    auto_generate_on_health_sync: bool = True
    auto_write_to_calendar: bool = False
    custom_mcp_servers: list[MCPServerConfig] = Field(default_factory=list)


class UserPreferencesUpdate(BaseModel):
    """Partial-update body for PATCH /preferences."""
    language: Language | None = None
    work_start: int | None = Field(default=None, ge=0, le=23)
    work_end: int | None = Field(default=None, ge=1, le=24)
    max_deep_work_minutes: int | None = Field(default=None, ge=15, le=180)
    auto_generate_on_health_sync: bool | None = None
    auto_write_to_calendar: bool | None = None
    custom_mcp_servers: list[MCPServerConfig] | None = None
