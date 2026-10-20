"""Ready-made MCP server configs for the servers worth attaching.

Attaching a server is already codeless — PATCH /preferences with a
`custom_mcp_servers` entry and `user_mcp.load_mcp_tools` picks it up. What's
missing without this module is the knowledge: which server is the real one
(Canvas search results are full of identical forks), which tools are worth
putting on the belt, and which ones write without asking.

So a preset carries three things: how to launch it, a curated allowlist, and the
credentials it needs. Nothing here or in the config it builds holds a secret —
`env_required` names the variables, `build()` writes ${VAR} references, and the
values are read from the environment only when the server is launched.

Verified 2026-10-19 against the GitHub API; `repo` is there so the next person
can re-check rather than trust this file.
"""
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
    # Non-secret settings baked into the launch, as (name, value) pairs — e.g.
    # server-side write gating. Kept apart from env_required so "what the user
    # must supply" stays an accurate list.
    env_fixed: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Tool arguments bound in code from the environment, as (argument, env var)
    # pairs. Hidden from the model's view of the tools; see
    # graphs/user_mcp.bind_tool_args.
    bound_args_from_env: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Tools the server performs immediately, with no preview or confirmation.
    # Kept out of the allowlist; listed so the exclusion is a decision on the
    # record rather than an oversight.
    unsafe_tools: tuple[str, ...] = field(default_factory=tuple)

    def missing_env(self) -> list[str]:
        return [k for k in self.env_required if not os.getenv(k)]

    def resolved_command(self) -> str | None:
        """Absolute path to the launcher, or None if it isn't installed.

        Checked because the failure is otherwise silent and confusing: the
        config looks right, the server just never loads. Most Python MCP servers
        launch with `uvx` (from astral's uv), which isn't installed by default.

        Falls back to looking beside the running interpreter, because a venv's
        bin directory isn't necessarily on PATH — the backend is normally
        started as `.venv/bin/uvicorn`, which doesn't activate the venv.
        """
        found = shutil.which(self.command)
        if found:
            return found
        beside = Path(sys.executable).parent / self.command
        return str(beside) if beside.exists() else None

    def command_available(self) -> bool:
        return self.resolved_command() is not None

    def blockers(self) -> list[str]:
        """Everything standing between this preset and a working tool belt."""
        out = [f"env:{k}" for k in self.missing_env()]
        if not self.command_available():
            out.append(f"command:{self.command}")
        return out

    def is_ready(self) -> bool:
        return not self.blockers()

    def build(self) -> MCPServerConfig:
        """The config to drop into `custom_mcp_servers`.

        Credentials go in as ${VAR} references, resolved only when the server is
        launched (graphs/user_mcp.expand_env_refs). The config is persisted to
        preferences and returned by GET /preferences, so it must never carry the
        values themselves — and a stored copy would outlive a rotated token.
        """
        return MCPServerConfig(
            name=self.key,
            transport="stdio",
            # Absolute path, so the server launches the same whether or not the
            # venv is on PATH.
            command=self.resolved_command() or self.command,
            args=list(self.args),
            env={**dict(self.env_fixed), **{k: f"${{{k}}}" for k in self.env_required}},
            tool_allowlist=list(self.tool_allowlist),
            bound_args={arg: f"${{{var}}}" for arg, var in self.bound_args_from_env},
        )


CANVAS = MCPPreset(
    key="canvas",
    title="Canvas LMS",
    repo="https://github.com/vishalsachdev/canvas-mcp",
    summary="课程作业、截止日期、公告、syllabus。截止日期直接变成可排的任务。",
    env_required=("CANVAS_API_TOKEN", "CANVAS_API_URL"),
    command="uvx",
    # PyPI package is canvas-mcp, but its executable is canvas-mcp-server, so a
    # bare `uvx canvas-mcp` finds nothing to run.
    args=("--from", "canvas-mcp", "canvas-mcp-server"),
    # Server-side gating, on top of our allowlist. CANVAS_ROLE=student registers
    # only student tools (~47); ALLOWED_WRITE_TOOLS=none removes every tool with
    # side effects at startup — on stdio the default is "everything". A write
    # tool that doesn't exist can't be talked into by instructions planted in
    # course content; a confirmation the agent can redeem itself would not stop it.
    env_fixed=(("CANVAS_ROLE", "student"), ("ALLOWED_WRITE_TOOLS", "none")),
    # Read-only, and narrowed to what a scheduling agent actually uses. The
    # server ships 100+ tools; a full belt bloats the prompt and degrades tool
    # selection, which is what tool_allowlist exists to prevent.
    # Names checked against the real server's tool list (2026-10-20).
    tool_allowlist=(
        "get_my_upcoming_assignments",
        "get_my_todo_items",
        "get_my_submission_status",
        "get_assignment_details",
        "list_my_announcements",
        "list_courses",
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
        "token 30 天就过期。server 端已设 ALLOWED_WRITE_TOOLS=none，"
        "三个会直接写入、没有预览步骤的工具根本不会注册（见 unsafe_tools）。"
    ),
)

GOOGLE_WORKSPACE = MCPPreset(
    key="google-workspace",
    title="Gmail + Google Calendar",
    repo="https://github.com/taylorwilsdon/google_workspace_mcp",
    summary="读邮件找 deadline 和面试邀请，抽出来用 add_to_schedule 排进日程。",
    env_required=("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "USER_GOOGLE_EMAIL"),
    command="uvx",
    # --read-only makes the server request only gmail.readonly + calendar.readonly
    # from Google, so nothing can be sent or changed even if a tool slipped onto
    # the belt. --single-user uses the one cached credential instead of
    # per-session mapping, which is what a personal backend wants.
    args=("workspace-mcp", "--tools", "gmail", "calendar",
          "--read-only", "--tool-tier", "core", "--single-user"),
    # The OAuth callback listener defaults to port 8000 — the same port as this
    # backend. OAUTHLIB_INSECURE_TRANSPORT allows the http://localhost callback.
    env_fixed=(("WORKSPACE_MCP_PORT", "8765"), ("OAUTHLIB_INSECURE_TRANSPORT", "1")),
    # Every Gmail/Calendar tool requires user_google_email — even with
    # USER_GOOGLE_EMAIL set server-side, the schema still demands it. Which
    # account to read is not the model's call, so it's bound in code.
    bound_args_from_env=(("user_google_email", "USER_GOOGLE_EMAIL"),),
    # Names checked against the real server's tool list (2026-10-20). An earlier
    # guess, list_calendar_events, doesn't exist and would have been filtered
    # out silently.
    tool_allowlist=(
        "search_gmail_messages",
        "get_gmail_message_content",
        "get_gmail_messages_content_batch",
        "list_calendars",
        "get_events",
    ),
    notes=(
        "Google Cloud Console 里建 OAuth client，类型选 **Desktop app**（本地 stdio "
        "运行不需要配 redirect URI）。个人 gmail.com 没有组织，consent screen 只能"
        "选 External，并把自己加进 test users。\n"
        "首次让 agent 读邮件时，server 会自动在浏览器打开 Google 授权页，回调走 "
        "localhost:8765；授权后再问一次即可，凭据会缓存，之后不再弹。\n"
        "Google 2026-11-11 那条「必须勾选 AI-powered agent」的规定只针对 Google "
        "自己托管的 MCP server；这个社区版走的是普通 Gmail/Calendar REST API，"
        "不受影响。将来若换官方 Gmail MCP，需要另建一个勾选了该项的 client。"
    ),
)


# Official reference servers. Of the original set only seven are still
# maintained — twelve (including Google Maps, Slack, GitHub) were moved to
# modelcontextprotocol/servers-archived, so anything named
# @modelcontextprotocol/server-* in an older guide needs checking first.
# These two need no credentials at all.

TIME = MCPPreset(
    key="time",
    title="时间与时区",
    repo="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    summary="时区换算。问「波士顿周三上午十点在国内是几点」这类问题时用。",
    env_required=(),
    command="uvx",
    args=("mcp-server-time",),
    tool_allowlist=("get_current_time", "convert_time"),
    notes=(
        "官方参考实现，无需凭据。注意本项目的日期算术是确定性代码在做"
        "（ARCHITECTURE §10.4），这个工具只用于回答用户的时区问题，"
        "不参与排程计算。"
    ),
)

FETCH = MCPPreset(
    key="fetch",
    title="抓网页",
    repo="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    summary="把网页转成 markdown 读。课程网站、招聘页面上的截止日期可以直接抓。",
    env_required=(),
    command="uvx",
    args=("mcp-server-fetch",),
    tool_allowlist=("fetch",),
    notes=(
        "官方参考实现，无需凭据。会按 agent 给的 URL 出网——抓到的内容是"
        "不可信输入，当数据看，不当指令看。"
    ),
)

PRESETS: dict[str, MCPPreset] = {
    p.key: p for p in (CANVAS, GOOGLE_WORKSPACE, TIME, FETCH)
}
