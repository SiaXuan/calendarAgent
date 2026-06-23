"""
Agent eval scenarios (Phase: 核心闭环 S5).

A small regression set that exercises the agent's genuine autonomy points.
Each scenario seeds a known schedule + a user message, declares the acceptable
terminal state(s), and an optional content check on the resulting blocks.

Run against the REAL LLM via tests/eval/run_eval.py — this catches behavior
regressions when you change the prompt or tools. The harness logic itself is
covered deterministically by tests/test_agent_run.py (fake LLM); this set
measures whether the real agent makes sensible *decisions*.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from models.schedule import BlockType, DaySchedule, TimeBlock
from models.task import CognitiveLoad, TaskKind

EVAL_DATE = date(2026, 6, 23)

# High-energy morning, low afternoon — so "move deep work to peak" has a clear target.
HIGH_MORNING = [0.2] * 24
for _h in range(8, 12):
    HIGH_MORNING[_h] = 0.9
for _h in range(12, 22):
    HIGH_MORNING[_h] = 0.45
FLAT = [0.5] * 24


def _blk(task_id, title, h0, m0, h1, m1, load=CognitiveLoad.deep,
         kind=TaskKind.analytical, btype=BlockType.scheduled):
    return TimeBlock(
        start=datetime(2026, 6, 23, h0, m0), end=datetime(2026, 6, 23, h1, m1),
        block_type=btype, task_id=task_id, title=title,
        cognitive_load=load, task_kind=kind,
    )


@dataclass
class Scenario:
    name: str
    message: str
    blocks: list[TimeBlock]
    energy_curve: list[float]
    expect_terminals: set[str]
    # check(result_blocks, result) -> (passed, detail)
    check: Callable | None = None
    note: str = ""


def _result_blocks(res):
    if res.proposal:
        return res.proposal["preview"].blocks
    if res.schedule:
        return res.schedule.blocks
    return []


# ─── scenarios ───────────────────────────────────────────────────────────────

def _deep_in_morning(blocks, res):
    deep = [b for b in blocks if "深度写作" in b.title]
    if not deep:
        return False, "找不到深度写作 block"
    hours = [b.start.hour for b in deep]
    return all(h < 12 for h in hours), f"深度写作 start hours={hours}（期望<12）"


def _leetcode_gone(blocks, res):
    left = [b for b in blocks if "LeetCode" in b.title]
    return len(left) == 0, f"结果里还剩 {len(left)} 个 LeetCode block"


SCENARIOS: list[Scenario] = [
    Scenario(
        name="move_deep_to_morning",
        message="我下午精力差，把深度写作挪到上午精力最好的时候",
        blocks=[
            _blk("t_write", "深度写作：论文初稿", 15, 0, 16, 0),
            _blk("t_admin", "回邮件", 10, 0, 10, 30, load=CognitiveLoad.light, kind=TaskKind.admin),
        ],
        energy_curve=HIGH_MORNING,
        expect_terminals={"success", "proposal"},
        check=_deep_in_morning,
        note="自主: 看能量曲线把深度任务放到上午",
    ),
    Scenario(
        name="ambiguous_meeting",
        message="把那个会议往后挪",
        blocks=[
            _blk("m1", "项目会议", 10, 0, 11, 0, btype=BlockType.fixed),
            _blk("m2", "客户会议", 14, 0, 15, 0, btype=BlockType.fixed),
        ],
        energy_curve=FLAT,
        expect_terminals={"clarification"},
        note="自主: 发现'会议'有歧义 → 反问而非瞎猜",
    ),
    Scenario(
        name="out_of_scope",
        message="帮我订一张明天去北京的高铁票",
        blocks=[_blk("t1", "写代码", 10, 0, 11, 0)],
        energy_curve=FLAT,
        expect_terminals={"degraded"},
        note="自主: 超出能力 → 诚实兜底",
    ),
    Scenario(
        name="remove_task",
        message="今天不想做 leetcode 了，删掉它",
        blocks=[
            _blk("t_lc", "完成 LeetCode 刷题", 10, 0, 10, 30),
            _blk("t_read", "读论文", 11, 0, 12, 0),
        ],
        energy_curve=FLAT,
        expect_terminals={"proposal", "success"},
        check=_leetcode_gone,
        note="删除=major → Proposal; 结果不含 leetcode",
    ),
    Scenario(
        name="light_day_no_op_ok",
        message="今天安排还行吧？",
        blocks=[_blk("t1", "读论文", 10, 0, 11, 0, load=CognitiveLoad.medium)],
        energy_curve=FLAT,
        expect_terminals={"no_change", "clarification", "success"},
        note="纯询问/轻量 → 不该误删误改",
    ),
]
