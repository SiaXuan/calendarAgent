"""Ready-made MCP server configs for the servers worth attaching.

Attaching a server is already codeless — PATCH /preferences with a
`custom_mcp_servers` entry and `user_mcp.load_mcp_tools` picks it up. What's
missing without this module is the knowledge: which server is the real one
(Canvas search results are full of identical forks), which tools are worth
putting on the belt, and which ones write without asking.

So a preset carries three things: how to launch it, a curated allowlist, and the
credentials it needs. Nothing here holds a secret — `env_required` names the
variables, and `build()` reads them from the environment at call time.

Verified 2026-10-19 against the GitHub API; `repo` is there so the next person
can re-check rather than trust this file.
"""
import os
from dataclasses import dataclass, field

from models.user import MCPServerConfig


@dataclass(frozen=True)
class MCPPreset:
    key: str
    title: str
    repo: str
    summary: str
    env_required: tuple[str, ...]
    tool_allowlist: tuple[str, ...]
    command: str
    args: tuple[str, ...]
    notes: str = ""
    # Tools the server performs immediately, with no preview or confirmation.
    # Kept out of the allowlist; listed so the exclusion is a decision on the
    # record rather than an oversight.
    unsafe_tools: tuple[str, ...] = field(default_factory=tuple)

    def missing_env(self) -> list[str]:
        return [k for k in self.env_required if not os.getenv(k)]

    def is_ready(self) -> bool:
        return not self.missing_env()

    def build(self) -> MCPServerConfig:
        """The config to drop into `custom_mcp_servers`.

        Reads credentials from the environment now, so a preset can sit in this
        file indefinitely without ever holding one.
        """
        return MCPServerConfig(
            name=self.key,
            transport="stdio",
            command=self.command,
            args=list(self.args),
            env={k: os.getenv(k, "") for k in self.env_required},
            tool_allowlist=list(self.tool_allowlist),
        )


CANVAS = MCPPreset(
    key="canvas",
    title="Canvas LMS",
    repo="https://github.com/vishalsachdev/canvas-mcp",
    summary="课程作业、截止日期、公告、syllabus。截止日期直接变成可排的任务。",
    env_required=("CANVAS_API_TOKEN", "CANVAS_API_URL"),
    command="uvx",
    args=("canvas-mcp",),
    # Read-only, and narrowed to what a scheduling agent actually uses. The
    # server ships 100+ tools; a full belt bloats the prompt and degrades tool
    # selection, which is what tool_allowlist exists to prevent.
    tool_allowlist=(
        "get_my_upcoming_assignments",
        "get_my_todo_items",
        "get_my_submission_status",
        "list_my_announcements",
        "get_course_details",
    ),
    unsafe_tools=(
        "create_planner_note",
        "mark_planner_item_complete",
        "create_personal_calendar_event",
    ),
    notes=(
        "CANVAS_API_URL 必须带 /api/v1。token 在 Canvas 的 Account → Settings → "
        "New Access Token 生成；有的学校不开自助发放，要走 IT 工单，也有学校的 "
        "token 30 天就过期。三个会直接写入、没有预览步骤的工具已排除在 allowlist "
        "之外（见 unsafe_tools）——要用它们得先补一道确认门。"
    ),
)

GOOGLE_WORKSPACE = MCPPreset(
    key="google-workspace",
    title="Gmail + Google Calendar",
    repo="https://github.com/taylorwilsdon/google_workspace_mcp",
    summary="读邮件找 deadline 和面试邀请，抽出来用 add_to_schedule 排进日程。",
    env_required=("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"),
    command="uvx",
    args=("workspace-mcp",),
    tool_allowlist=(
        "search_gmail_messages",
        "get_gmail_message_content",
        "list_calendar_events",
    ),
    notes=(
        "要自己在 Google Cloud Console 建 OAuth client：个人 gmail.com 没有组织，"
        "只能选 External 并把自己加进 test users。两个坑——(1) client 必须勾选 "
        "'Use this client for an AI-powered agent'，2026-11-11 起没勾的会被所有 "
        "Google Workspace MCP server 拒绝；(2) 没有 service account 路径，所以"
        "需要人在场授权，无人值守的后台任务走不通这条路。\n"
        "官方的 Gmail MCP（gmailmcp.googleapis.com）权限模型更合适"
        "（只读 + 起草，发不出去），但还在 developer preview，而且自建后端该用"
        "什么 redirect URI 官方文档没写——先用社区这个，它顺带把 Calendar 也给了。"
    ),
)

PRESETS: dict[str, MCPPreset] = {p.key: p for p in (CANVAS, GOOGLE_WORKSPACE)}
