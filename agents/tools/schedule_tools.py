"""
Schedule + calc tools for the conversational agent (Phase: 核心闭环 S2).

Each tool is a uniform interface (name + typed args + docstring) over a backend
— here the backend is the run-scoped `ScheduleScratch` (mutations) or the pure
`agents/calc.py` + `agents/solver.py` functions (computation).

Tools are STATEFUL: they close over one ScheduleScratch instance per agent run.
`make_schedule_tools(scratch)` returns the bound tool list to hand to the agent.

The LLM addresses blocks by their scratch id ("b1"…) shown in `get_schedule()`.
Mutations return the refreshed projection so the LLM never operates on stale state.
"""
from datetime import date, datetime

from langchain_core.tools import StructuredTool

from agents.calc import capacity_check as _capacity_check
from agents.calc import working_hours_until as _working_hours_until
from agents.scratch import ScheduleScratch
from models.solver import FixedInterval


def make_schedule_tools(scratch: ScheduleScratch) -> list:
    """Return LangChain tools bound to this run's scratch."""

    def get_schedule() -> str:
        """查看当前(草稿)日程。返回每个 block 一行，含其 id(如 b1)、时间、类型。
        修改 block 前先看这个拿到 id。"""
        return scratch.render()

    def move_block(block_id: str, new_start_iso: str) -> str:
        """把某个 block 移到同一天的新开始时间(时长不变)。
        block_id 来自 get_schedule(如 'b1')；new_start_iso 形如 '2026-06-15T11:00:00'。
        只能移 scheduled/suggested 类型；返回更新后的日程。"""
        try:
            start = datetime.fromisoformat(new_start_iso)
        except ValueError:
            return f"错误：new_start_iso 格式不对: {new_start_iso!r}"
        try:
            return scratch.move_block(block_id, start)
        except ValueError as e:
            return f"错误：{e}"

    def remove_block(block_id: str) -> str:
        """删除一个 agent 排的 block(scheduled/suggested)。返回更新后的日程。
        不能删 fixed(用户事件)/meal。"""
        try:
            return scratch.remove_block(block_id)
        except ValueError as e:
            return f"错误：{e}"

    def add_fixed_event(title: str, start_iso: str, end_iso: str) -> str:
        """加一个固定事件(如牙医预约)，其他任务会绕开它。
        start_iso/end_iso 形如 '2026-06-15T15:00:00'。返回更新后的日程。"""
        try:
            start, end = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
        except ValueError:
            return "错误：时间格式不对"
        if end <= start:
            return "错误：结束时间必须晚于开始时间"
        return scratch.add_fixed_event(title, start, end)

    def capacity_check_tool() -> str:
        """检查今天容量：返回 空闲分钟/已占分钟/缺口/是否超额。
        在判断'今天塞不塞得下'时调它，别自己估。"""
        committed = sum(
            int((b.end - b.start).total_seconds() // 60)
            for b in scratch.committed_blocks()
            if b.block_type.value in ("scheduled", "suggested")
        )
        fixed = [
            FixedInterval(start=b.start, end=b.end)
            for b in scratch.committed_blocks()
            if b.block_type.value in ("fixed", "meal")
        ]
        r = _capacity_check(
            scratch.target_date, fixed, committed,
            work_start_hour=scratch.work_start_hour, work_end_hour=scratch.work_end_hour,
        )
        return (f"空闲 {r['free_min']}min / 已占 {r['committed_min']}min / "
                f"缺口 {r['deficit_min']}min / 超额={r['oversubscribed']}")

    def working_hours_until_tool(deadline_iso: str) -> str:
        """算从今天到某截止日(含)之间还有多少工作分钟。判断紧急度/能不能赶上 ddl 时用，别自己数日期。
        deadline_iso 形如 '2026-06-20'。"""
        try:
            dl = date.fromisoformat(deadline_iso)
        except ValueError:
            return f"错误：日期格式不对: {deadline_iso!r}"
        r = _working_hours_until(
            dl, today=scratch.target_date,
            work_start_hour=scratch.work_start_hour, work_end_hour=scratch.work_end_hour,
        )
        return (f"{r['days']} 天 / {r['working_minutes']} 工作分钟"
                + (" / 已过期" if r["is_overdue"] else ""))

    return [
        StructuredTool.from_function(get_schedule),
        StructuredTool.from_function(move_block),
        StructuredTool.from_function(remove_block),
        StructuredTool.from_function(add_fixed_event),
        StructuredTool.from_function(capacity_check_tool, name="capacity_check"),
        StructuredTool.from_function(working_hours_until_tool, name="working_hours_until"),
    ]
