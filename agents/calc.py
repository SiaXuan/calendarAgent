"""
Calculation helpers exposed as agent tools (Phase: 核心闭环 S1).

LLMs are unreliable at exact arithmetic and date math — so the agent must NOT
"eyeball" capacity or count working hours. It calls these deterministic tools.
This is the "agent 编排 / 工具计算" division: the agent decides WHICH calc to
run and what to do with the number; the calc itself is exact and testable.

Pure functions, no LLM, no I/O beyond reading the passed-in data.
"""
from datetime import date, datetime, timedelta

from models.solver import FixedInterval


def working_hours_until(
    deadline: date,
    *,
    today: date,
    work_start_hour: int = 8,
    work_end_hour: int = 22,
    include_weekends: bool = True,
) -> dict:
    """
    Exact working-hour budget between `today` (inclusive) and `deadline` (inclusive).

    Returns {days, working_minutes, is_overdue}. Used by the agent to judge
    urgency / whether a task can still fit before its deadline.
    """
    if deadline < today:
        return {"days": 0, "working_minutes": 0, "is_overdue": True}
    per_day = max(0, (work_end_hour - work_start_hour)) * 60
    days = 0
    minutes = 0
    cursor = today
    while cursor <= deadline:
        if include_weekends or cursor.weekday() < 5:
            minutes += per_day
            days += 1
        cursor += timedelta(days=1)
    return {"days": days, "working_minutes": minutes, "is_overdue": False}


def capacity_check(
    target_date: date,
    fixed_blocks: list[FixedInterval],
    committed_min: int,
    *,
    work_start_hour: int = 8,
    work_end_hour: int = 22,
) -> dict:
    """
    Free vs committed minutes for a single day.

    `committed_min` = total minutes of must-do / already-scheduled work the agent
    wants to fit. Returns {free_min, committed_min, deficit_min, oversubscribed}.

    This is how the agent DETECTS over-subscription (the user's core insight)
    before acting — rather than guessing.
    """
    day_minutes = max(0, (work_end_hour - work_start_hour)) * 60
    fixed_min = sum(
        int((f.end - f.start).total_seconds() // 60)
        for f in fixed_blocks if f.start.date() == target_date
    )
    free_min = max(0, day_minutes - fixed_min)
    deficit = max(0, committed_min - free_min)
    return {
        "free_min": free_min,
        "committed_min": committed_min,
        "deficit_min": deficit,
        "oversubscribed": deficit > 0,
    }
