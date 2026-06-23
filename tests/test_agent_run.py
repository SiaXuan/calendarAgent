"""
Tests for the conversational agent harness (Phase: 核心闭环 S3).

We don't use a real LLM. Instead we monkeypatch `create_react_agent` with a fake
that invokes the bound tools in a SCRIPTED sequence (mutating the real scratch),
then returns a final message. This deterministically exercises the full harness:
tool execution → scratch mutation → diff → impact gate → commit/Proposal/terminal.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

import graphs.agent_run as ar
from graphs.agent_run import confirm_proposal, run_chat_agent
from models.schedule import BlockType, DaySchedule, TimeBlock
from models.task import CognitiveLoad, TaskKind


D = date(2026, 6, 15)
pytestmark = pytest.mark.asyncio


def _scheduled(task_id, title, h0, h1):
    return TimeBlock(
        start=datetime(2026, 6, 15, h0, 0), end=datetime(2026, 6, 15, h1, 0),
        block_type=BlockType.scheduled, task_id=task_id, title=title,
        cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
    )


def _seed(blocks):
    from storage import schedule_store, schedule_version
    schedule_store[D] = DaySchedule(
        date=D, energy_curve=[0.5] * 24, blocks=blocks,
        unscheduled=[], health_summary="ok",
    )
    schedule_version[D] = 1


class _FakeAgent:
    """Stand-in for a compiled react agent. Runs a scripted tool sequence."""
    def __init__(self, tools, script_calls, final_text):
        self._tools = {t.name: t for t in tools}
        self._calls = script_calls
        self._final = final_text

    async def ainvoke(self, inp, config=None):
        msgs = []
        for tname, targs in self._calls:
            out = self._tools[tname].invoke(targs)
            msgs.append(AIMessage(content="", tool_calls=[
                {"name": tname, "args": targs, "id": tname}
            ]))
        msgs.append(AIMessage(content=self._final))
        return {"messages": msgs}


@pytest.fixture
def patch_agent(monkeypatch):
    """Returns a setter; call set(script_calls, final_text) before run_chat_agent."""
    holder = {}

    def fake_factory(model, tools, prompt=None, **kw):
        return _FakeAgent(tools, holder.get("calls", []), holder.get("final", "done"))

    monkeypatch.setattr(ar, "create_react_agent", fake_factory)

    def setter(calls, final="done"):
        holder["calls"] = calls
        holder["final"] = final
    return setter


# ─── terminal states ─────────────────────────────────────────────────────────

async def test_minor_change_commits(clean_stores, patch_agent):
    _seed([_scheduled("t1", "Work", 9, 10)])
    patch_agent([("move_block", {"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"})],
                final="挪好了")
    res = await run_chat_agent(D, "把工作挪到下午2点")
    assert res.terminal_state == "success"
    # committed: schedule_store reflects the move
    from storage import schedule_store, current_version
    moved = schedule_store[D].blocks[0]
    assert moved.start == datetime(2026, 6, 15, 14, 0)
    assert current_version(D) == 2   # bumped


async def test_major_change_returns_proposal_without_commit(clean_stores, patch_agent):
    _seed([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])
    patch_agent([
        ("move_block", {"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"}),
        ("move_block", {"block_id": "b2", "new_start_iso": "2026-06-15T16:00:00"}),
    ], final="改动较大")
    res = await run_chat_agent(D, "重排下午")
    assert res.terminal_state == "proposal"
    assert res.proposal is not None
    # NOT committed — original positions intact
    from storage import schedule_store, pending_proposals, current_version
    assert schedule_store[D].blocks[0].start == datetime(2026, 6, 15, 9, 0)
    assert current_version(D) == 1   # not bumped
    assert D in pending_proposals


async def test_ask_user_is_clarification(clean_stores, patch_agent):
    _seed([_scheduled("t1", "Work", 9, 10)])
    patch_agent([("ask_user", {"question": "哪个会议?"})], final="哪个会议?")
    res = await run_chat_agent(D, "把那个会议挪开")
    assert res.terminal_state == "clarification"
    assert "会议" in res.message
    from storage import current_version
    assert current_version(D) == 1   # nothing committed


async def test_report_blocked_is_degraded(clean_stores, patch_agent):
    _seed([_scheduled("t1", "Work", 9, 10)])
    patch_agent([("report_blocked", {"reason": "我不能订机票"})], final="做不到")
    res = await run_chat_agent(D, "帮我订机票")
    assert res.terminal_state == "degraded"
    assert "机票" in res.message


async def test_no_tool_calls_is_no_change(clean_stores, patch_agent):
    _seed([_scheduled("t1", "Work", 9, 10)])
    patch_agent([], final="今天不重，不用调")
    res = await run_chat_agent(D, "我有点累")
    assert res.terminal_state == "no_change"
    assert res.message == "今天不重，不用调"


async def test_no_schedule_is_degraded(clean_stores, patch_agent):
    patch_agent([], final="x")
    res = await run_chat_agent(D, "调整")
    assert res.terminal_state == "degraded"


async def test_recursion_error_falls_back_to_degraded(clean_stores, monkeypatch):
    _seed([_scheduled("t1", "Work", 9, 10)])
    from langgraph.errors import GraphRecursionError

    class _Boom:
        def __init__(self, tools): pass
        async def ainvoke(self, inp, config=None):
            raise GraphRecursionError("loop")

    monkeypatch.setattr(ar, "create_react_agent", lambda model, tools, prompt=None, **kw: _Boom(tools))
    res = await run_chat_agent(D, "绕圈")
    assert res.terminal_state == "degraded"


# ─── confirm_proposal ────────────────────────────────────────────────────────

async def test_confirm_applies_when_version_matches(clean_stores, patch_agent):
    _seed([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])
    patch_agent([
        ("move_block", {"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"}),
        ("move_block", {"block_id": "b2", "new_start_iso": "2026-06-15T16:00:00"}),
    ], final="改动较大")
    await run_chat_agent(D, "重排")
    res = confirm_proposal(D)
    assert res.terminal_state == "success"
    from storage import schedule_store, pending_proposals
    starts = sorted(b.start.hour for b in schedule_store[D].blocks)
    assert starts == [14, 16]
    assert D not in pending_proposals   # cleared


async def test_confirm_invalidated_when_version_changed(clean_stores, patch_agent):
    _seed([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])
    patch_agent([
        ("move_block", {"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"}),
        ("move_block", {"block_id": "b2", "new_start_iso": "2026-06-15T16:00:00"}),
    ], final="x")
    await run_chat_agent(D, "重排")
    # Simulate an external schedule change before confirm
    from storage import bump_schedule_version, pending_proposals, schedule_store
    bump_schedule_version(D)
    res = confirm_proposal(D)
    assert res.terminal_state == "clarification"
    assert D not in pending_proposals   # stale proposal discarded
    # original positions untouched
    assert schedule_store[D].blocks[0].start.hour == 9


async def test_followup_builds_on_pending_proposal(clean_stores, patch_agent):
    """
    Multi-turn: turn 1 proposes deleting a block (major, not applied). Turn 2
    must start from the PROPOSED state (deleted block gone), not the original
    schedule — otherwise the deletion 'comes back'. Also turn 2's agent should
    receive turn 1 in its message history.
    """
    _seed([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])

    # Turn 1: delete A → major → proposal (A removed in staged state)
    patch_agent([("remove_block", {"block_id": "b1"})], final="删了A")
    r1 = await run_chat_agent(D, "删掉A")
    assert r1.terminal_state == "proposal"
    from storage import pending_proposals, chat_sessions
    assert D in pending_proposals
    staged_titles = {b.title for b in pending_proposals[D]["staged_blocks"]}
    assert "A" not in staged_titles   # A removed in the staged proposal

    # Turn 2: capture the scratch the agent sees by recording rendered state via a tool call.
    seen = {}

    def fake_factory(model, tools, prompt=None, **kw):
        # capture how many blocks the scratch starts with (via get_schedule tool)
        get_sched = next(t for t in tools if t.name == "get_schedule")
        seen["render"] = get_sched.invoke({})
        seen["history_len"] = None
        class _A:
            async def ainvoke(self, inp, config=None):
                seen["history_len"] = len(inp["messages"])
                return {"messages": [AIMessage(content="ok")]}
        return _A()

    import graphs.agent_run as ar
    ar.create_react_agent = fake_factory
    await run_chat_agent(D, "也把B挪走")

    # Turn 2 scratch started from the proposed state → A is NOT present
    assert "A" not in seen["render"]
    assert "B" in seen["render"]
    # History was passed (turn 1 user+assistant = 2 msgs + turn 2 user = 3)
    assert seen["history_len"] >= 3


async def test_regenerate_clears_chat_session(clean_stores, patch_agent, mock_sonnet, mock_caldav, mock_reminders_sync):
    """A full regenerate drops the conversation history + pending proposal."""
    _seed([_scheduled("t1", "A", 9, 10)])
    patch_agent([("ask_user", {"question": "哪个?"})], final="哪个?")
    await run_chat_agent(D, "改一下")
    from storage import chat_sessions
    assert D in chat_sessions
    # Full regenerate
    from agents.task_agent import _LLMSubtaskList
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[]))
    from graphs.schedule_graph import run_schedule_graph
    await run_schedule_graph(D)
    assert D not in chat_sessions


async def test_confirm_no_proposal_is_degraded(clean_stores):
    _seed([_scheduled("t1", "A", 9, 10)])
    res = confirm_proposal(D)
    assert res.terminal_state == "degraded"


async def test_confirm_expired_ttl(clean_stores, patch_agent):
    _seed([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])
    patch_agent([
        ("move_block", {"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"}),
        ("move_block", {"block_id": "b2", "new_start_iso": "2026-06-15T16:00:00"}),
    ], final="x")
    await run_chat_agent(D, "重排")
    # Force the proposal's created_at into the past
    from storage import pending_proposals
    pending_proposals[D]["created_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    res = confirm_proposal(D)
    assert res.terminal_state == "degraded"
    assert D not in pending_proposals
