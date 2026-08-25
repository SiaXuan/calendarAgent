"""Email triage flow (Phase D).

Runs a small ReAct agent over the user's attached email MCP tools to read today's
new mail and produce a structured EmailReport (needs-reply / newsletters / detected
invites / ads count). Read-only: it does NOT touch the calendar — detected invites
are surfaced so the user can ask the main agent to add one.

Graceful by design: if no email MCP server is attached (get_mcp_tools has no
mail-ish tools), returns EmailReport(connected=False) with a hint. The connected
path needs on-device validation once an Outlook/Gmail server is wired (real tool
names/shapes come from that server).
"""
from datetime import date, datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from agents.llm import sonnet
from graphs.user_mcp import get_mcp_tools
from models.email import EmailReport

# Tools whose name looks email-related. MCP servers name tools freely, so match
# broadly; anything matched is exposed to the triage agent.
_MAIL_HINTS = ("mail", "message", "outlook", "gmail", "inbox", "email", "thread")


def _mail_tools() -> list:
    return [t for t in get_mcp_tools() if any(h in t.name.lower() for h in _MAIL_HINTS)]


_PROMPT_BY_LANG = {
    "zh-CN": (
        "你是邮件整理助手。用可用的邮件工具读取用户**今天新收到**的邮件，然后给出结构化汇报：\n"
        "- needs_reply：需要用户回复的（老板/同事/客户/教授等真人来信、含问题或请求的）。\n"
        "- newsletters：订阅/资讯类，值得一提但不用回。\n"
        "- detected_invites：含会议/面试/笔试等带时间的邀请（gist 里写时间地点要点）。\n"
        "- filtered_ads：纯广告/促销的数量（不用逐条列）。\n"
        "- summary：一两句话的总体概括。\n"
        "只读，不要发信/改日历。用中文填写所有文本字段。读不到邮件就如实说明。"
    ),
    "en": (
        "You are an email triage assistant. Use the available mail tools to read the user's mail "
        "received TODAY, then return a structured report:\n"
        "- needs_reply: real messages that need a reply (people, questions, requests).\n"
        "- newsletters: subscriptions/updates worth mentioning but no reply needed.\n"
        "- detected_invites: meeting/interview/test invites with a time (put time+place in gist).\n"
        "- filtered_ads: count of pure promotional mail (don't list each).\n"
        "- summary: a one or two sentence overview.\n"
        "Read-only — do not send mail or touch the calendar. If you can't read mail, say so."
    ),
}


def _prompt(language: str) -> str:
    return _PROMPT_BY_LANG.get(language, _PROMPT_BY_LANG["en"])


async def run_email_triage(target_date: date, language: str = "en") -> EmailReport:
    now = datetime.now().isoformat(timespec="seconds")
    tools = _mail_tools()
    if not tools:
        return EmailReport(
            connected=False,
            generated_at=now,
            note=(
                "未连接邮箱。在 偏好设置 的 custom_mcp_servers 里配置一个 Outlook/Gmail MCP server 后再拉取。"
                if language.startswith("zh")
                else "No mailbox connected. Attach an Outlook/Gmail MCP server in preferences, then pull."
            ),
        )

    system = SystemMessage(content=_prompt(language))
    agent = create_react_agent(sonnet, tools, prompt=system, response_format=EmailReport)
    ask = (
        f"整理 {target_date.isoformat()} 今天新收到的邮件。"
        if language.startswith("zh")
        else f"Triage the mail received today ({target_date.isoformat()})."
    )
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=ask)]})
    except Exception as exc:  # network / tool / model — degrade, never 500 the panel
        return EmailReport(
            connected=True, generated_at=now,
            note=f"Triage failed: {exc}",
        )

    report = result.get("structured_response")
    if isinstance(report, EmailReport):
        report.connected = True
        report.generated_at = now
        return report
    # Model didn't produce structured output — return its text as the summary.
    msgs = result.get("messages", [])
    text = msgs[-1].content if msgs else ""
    return EmailReport(connected=True, generated_at=now, summary=str(text))
