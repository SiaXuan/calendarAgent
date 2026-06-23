"""
Conversational schedule agent — the real ReAct agent (Phase: 核心闭环 S3).

This is where autonomy lives. Unlike the old `chat_agent` (one structured-output
call filling a fixed 6-field schema), here an LLM decides AT RUNTIME which tools
to call, how many times, in what order, and what to do with results.

Harness flow (from plan's 状态感知 + 分级 commit + 鲁棒性 sections):
  1. freeze schedule_store[date] → ScheduleScratch (+ base_version)
  2. run create_react_agent over the bound tool belt on the scratch
  3. determine terminal state:
       ask_user called      → Clarification
       report_blocked called→ Degraded
       loop blew limit / API died → Degraded (fallback)
       else diff the scratch:
         empty                → no_change (agent decided nothing needed)
         minor (gate)         → atomic commit → Success
         major (gate)         → stage Proposal (no commit) → Proposal
  4. log the run for replay/eval

The impact gate (classify_impact) is deterministic and OUTSIDE the agent's
reach — the agent cannot talk itself into auto-committing a major change.
"""
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from agents.llm import sonnet
from agents.scratch import ScheduleScratch, classify_impact
from agents.tools.schedule_tools import make_schedule_tools
from api.preferences import get_current_prefs
from models.schedule import DaySchedule
from storage import (
    agent_run_log,
    bump_schedule_version,
    chat_sessions,
    current_version,
    pending_proposals,
    schedule_store,
)

_log = logging.getLogger("dayflow")

_RECURSION_LIMIT = 16          # ~8 tool-call rounds (model+tools = 2 steps each)
_PROPOSAL_TTL_MIN = 5
_MAX_HISTORY_MSGS = 12         # cap conversation context fed to the agent


class AgentChatResult(BaseModel):
    terminal_state: str   # success | proposal | clarification | degraded | no_change
    message: str
    schedule: DaySchedule | None = None
    proposal: dict | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_system_prompt(scratch: ScheduleScratch, language, memory_bullets: list[str]) -> str:
    mem = ""
    if memory_bullets:
        mem = "\n已知用户偏好(纳入判断):\n" + "\n".join(f"  - {b}" for b in memory_bullets)
    return f"""你是用户的日程助理，负责调整 {scratch.target_date.isoformat()} 的日程。

当前日程(每行一个 block，[bN] 是它的 id):
{scratch.render()}
{mem}

你可以调用工具来查看和修改日程。原则:
- 先用 get_schedule / capacity_check / working_hours_until 了解情况，别凭空估算。
- 用 move_block / remove_block / add_fixed_event 调整。只能动 scheduled/suggested，不许碰 fixed/meal。
- 你**只能管今天**这一天的日程。如果用户要把任务"挪到明天/改天"，你能做的只是用 remove_block 把它从今天移除，并**如实说明**「已从今天移除，明天的安排请到那天再生成」——**绝不要声称已经排到了别的某天**(你没有这个能力)。
- 请求**模糊或缺信息**时(比如有多个同名 block、不确定挪到哪)，调 ask_user(question) 问用户，别瞎猜。
- 现有工具**真的做不到**时(比如要订机票)，调 report_blocked(reason) 诚实说明。

回复给用户的最终消息，严格遵守:
- **只用 1-2 句话**说明你做了什么、关键原因。不要复述整个日程(改动会单独以可视化卡片展示)。
- **全程用 {getattr(language, 'value', 'zh-CN')}**，不要中英混杂。
- **纯文本**，不要 markdown 表格、不要 markdown 标题、不要罗列清单。
- **用任务的标题**称呼它，绝不要在消息里出现 b1/b2 这种内部 id。
"""


def _make_signal_tools(signals: dict) -> list:
    def ask_user(question: str) -> str:
        """当请求模糊或你需要用户给信息才能继续时调用，传你要问的问题。调用后请结束并把问题告诉用户。"""
        signals["clarification"] = question
        return "已记录待澄清问题，请结束本轮并向用户提问。"

    def report_blocked(reason: str) -> str:
        """当现有工具无法满足请求时调用，传做不到的原因。调用后请结束。"""
        signals["blocked"] = reason
        return "已记录无法完成，请结束本轮并向用户解释。"

    return [
        StructuredTool.from_function(ask_user),
        StructuredTool.from_function(report_blocked),
    ]


def _final_text(result: dict) -> str:
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        content = getattr(m, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            if text.strip():
                return text.strip()
    return ""


def _commit(target_date: date, scratch: ScheduleScratch) -> DaySchedule:
    """Atomic commit: replace blocks in schedule_store + bump version."""
    base = schedule_store[target_date]
    new = base.model_copy(update={"blocks": scratch.committed_blocks()})
    schedule_store[target_date] = new
    bump_schedule_version(target_date)
    return new


def _stage_proposal(target_date: date, scratch: ScheduleScratch, summary: str) -> dict:
    base = schedule_store[target_date]
    preview = base.model_copy(update={"blocks": scratch.committed_blocks()})
    proposal = {
        "proposal_id": str(uuid.uuid4()),
        "base_version": scratch.base_version,
        "staged_blocks": scratch.committed_blocks(),   # in-memory TimeBlock objs
        "summary": summary,
        "created_at": _now(),
        "preview": preview,                            # for frontend diff render
        "changes": [c.model_dump() for c in scratch.diff().changes],
    }
    pending_proposals[target_date] = proposal
    return proposal


def _log_run(target_date, user_message, terminal, result_text, tool_calls):
    agent_run_log.append({
        "date": target_date.isoformat(),
        "input": user_message,
        "terminal_state": terminal,
        "output": result_text,
        "tool_calls": tool_calls,
        "at": _now().isoformat(),
    })


def _fresh_proposal(target_date: date) -> dict | None:
    """Return the pending proposal for the date if it exists and hasn't expired."""
    prop = pending_proposals.get(target_date)
    if prop is None:
        return None
    if _now() - prop["created_at"] > timedelta(minutes=_PROPOSAL_TTL_MIN):
        return None
    return prop


def _history_messages(target_date: date) -> list:
    """Prior conversation turns as LangChain messages (text only, no tool calls)."""
    out = []
    for turn in chat_sessions.get(target_date, [])[-_MAX_HISTORY_MSGS:]:
        if turn["role"] == "user":
            out.append(HumanMessage(content=turn["content"]))
        else:
            out.append(AIMessage(content=turn["content"]))
    return out


def _record_turn(target_date: date, user_message: str, assistant_message: str) -> None:
    session = chat_sessions.setdefault(target_date, [])
    session.append({"role": "user", "content": user_message})
    session.append({"role": "assistant", "content": assistant_message})
    # Bound growth — keep only the most recent turns.
    if len(session) > _MAX_HISTORY_MSGS:
        chat_sessions[target_date] = session[-_MAX_HISTORY_MSGS:]


def clear_chat_session(target_date: date) -> None:
    """Drop conversation history (called on full regenerate — fresh schedule)."""
    chat_sessions.pop(target_date, None)


async def run_chat_agent(target_date: date, user_message: str) -> AgentChatResult:
    current = schedule_store.get(target_date)
    if current is None:
        return AgentChatResult(
            terminal_state="degraded",
            message="今天还没有日程，先生成一个再调整。",
        )

    prefs = get_current_prefs()
    from memory import retrieval
    memory_bullets = retrieval.for_chat()

    # Multi-turn continuity: if a major Proposal is pending (user is refining it
    # without having clicked Apply), build the scratch from the PROPOSED state so
    # follow-ups stack on it — not on the original schedule. Otherwise start from
    # the live schedule.
    pending = _fresh_proposal(target_date)
    base_blocks = pending["staged_blocks"] if pending else current.blocks

    scratch = ScheduleScratch(
        target_date, base_blocks, base_version=current_version(target_date),
        work_start_hour=prefs.work_start, work_end_hour=prefs.work_end,
        energy_curve=current.energy_curve,
    )
    signals: dict = {"clarification": None, "blocked": None}
    tools = make_schedule_tools(scratch) + _make_signal_tools(signals)
    system_prompt = _build_system_prompt(scratch, prefs.language, memory_bullets)

    agent = create_react_agent(sonnet, tools, prompt=system_prompt)

    try:
        result = await agent.ainvoke(
            {"messages": _history_messages(target_date) + [HumanMessage(content=user_message)]},
            config={"recursion_limit": _RECURSION_LIMIT},
        )
    except GraphRecursionError:
        _log_run(target_date, user_message, "degraded", "loop limit", 0)
        return AgentChatResult(
            terminal_state="degraded", schedule=current,
            message="这个调整有点绕，我没在合理步数内搞定。能不能说得更具体一点？",
        )
    except Exception as exc:
        _log.warning("agent run failed: %s", exc)
        _log_run(target_date, user_message, "degraded", str(exc), 0)
        return AgentChatResult(
            terminal_state="degraded", schedule=current,
            message="出了点问题，日程保持原样没动。",
        )

    text = _final_text(result)
    n_tool_calls = sum(
        len(getattr(m, "tool_calls", []) or []) for m in result.get("messages", [])
    )

    # Terminal state resolution (explicit signals first).
    if signals["clarification"]:
        _log_run(target_date, user_message, "clarification", signals["clarification"], n_tool_calls)
        _record_turn(target_date, user_message, signals["clarification"])
        return AgentChatResult(
            terminal_state="clarification", schedule=current,
            message=signals["clarification"],
        )
    if signals["blocked"]:
        _log_run(target_date, user_message, "degraded", signals["blocked"], n_tool_calls)
        _record_turn(target_date, user_message, signals["blocked"])
        return AgentChatResult(
            terminal_state="degraded", schedule=current, message=signals["blocked"],
        )

    diff = scratch.diff()
    if diff.is_empty:
        _log_run(target_date, user_message, "no_change", text, n_tool_calls)
        _record_turn(target_date, user_message, text or "看了下，今天不用调整。")
        return AgentChatResult(
            terminal_state="no_change", schedule=current,
            message=text or "看了下，今天不用调整。",
        )

    impact = classify_impact(diff)
    if impact == "minor":
        new = _commit(target_date, scratch)
        pending_proposals.pop(target_date, None)   # superseded by this commit
        _log_run(target_date, user_message, "success", text, n_tool_calls)
        _record_turn(target_date, user_message, text or "已调整。")
        return AgentChatResult(
            terminal_state="success", schedule=new,
            message=text or "已调整。",
        )

    # major → Proposal (no commit). Supersedes any prior pending proposal.
    proposal = _stage_proposal(target_date, scratch, text or "改动较大，请确认。")
    _log_run(target_date, user_message, "proposal", text, n_tool_calls)
    _record_turn(target_date, user_message, text or "改动较大，请确认。")
    return AgentChatResult(
        terminal_state="proposal", schedule=current,
        message=text or "这个改动比较大，给你预览一下，确认再生效。",
        proposal={
            "proposal_id": proposal["proposal_id"],
            "summary": proposal["summary"],
            "preview": proposal["preview"],
            "changes": proposal["changes"],
        },
    )


def confirm_proposal(target_date: date) -> AgentChatResult:
    """Apply a pending major Proposal after version + TTL checks (stale-Proposal safety)."""
    prop = pending_proposals.get(target_date)
    if prop is None:
        return AgentChatResult(terminal_state="degraded", message="没有待确认的调整。")

    # TTL expiry
    if _now() - prop["created_at"] > timedelta(minutes=_PROPOSAL_TTL_MIN):
        pending_proposals.pop(target_date, None)
        return AgentChatResult(
            terminal_state="degraded",
            message="这个调整建议过期了，重新说一下吧。",
            schedule=schedule_store.get(target_date),
        )

    # Optimistic-concurrency: schedule changed since proposal → invalidate.
    if current_version(target_date) != prop["base_version"]:
        pending_proposals.pop(target_date, None)
        return AgentChatResult(
            terminal_state="clarification",
            message="日程在你确认前变了，我没套用旧方案。要的话我按最新的重排一遍。",
            schedule=schedule_store.get(target_date),
        )

    base = schedule_store[target_date]
    new = base.model_copy(update={"blocks": prop["staged_blocks"]})
    schedule_store[target_date] = new
    bump_schedule_version(target_date)
    pending_proposals.pop(target_date, None)
    return AgentChatResult(terminal_state="success", schedule=new, message="已套用。")
