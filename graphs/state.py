"""
LangGraph state definitions for the schedule + adjust + task-chat graphs.

State is a TypedDict (not Pydantic BaseModel) because LangGraph nodes return
*partial* updates — node functions return e.g. `{"subtasks": [...]}` rather
than a full state object. TypedDict lets LangGraph merge those patches.

The Pydantic models from `models/` (HealthSnapshot, TimeBlock, Subtask, etc.)
are still used as the *element* types inside these TypedDicts — we only avoid
Pydantic at the top-level state container.
"""
from datetime import date
from typing import Annotated, TypedDict

from models.health import HealthSnapshot
from models.schedule import DaySchedule, FreeWindow, TimeBlock
from models.task import Subtask, Task
from models.user import Language


class ScheduleState(TypedDict, total=False):
    """State that flows through `schedule_graph` (full day generation)."""

    # ─── Inputs (filled at graph start) ────────────────────────────────────
    target_date: date
    language: Language
    snapshot: HealthSnapshot | None
    tasks: list[Task]

    # ─── Phase A: fan-out branches each fill their own piece ───────────────
    energy_curve: list[float]
    health_summary: str
    fixed_blocks: list[TimeBlock]
    free_windows: list[FreeWindow]

    # ─── Phase C: memory-aware ranking (filled later, default empty) ───────
    user_memory: list[str]

    # ─── Sequential stages after fan-out converges ─────────────────────────
    subtasks: list[Subtask]            # output of rank_tasks node
    instant_subtasks: list[Subtask]    # quick reminders split off from subtasks
    instant_blocks: list[TimeBlock]    # rendered from instant_subtasks
    meal_blocks: list[TimeBlock]
    sleep_start_hour: int
    sleep_end_hour: int

    # ─── Scheduler output ──────────────────────────────────────────────────
    scheduled_blocks: list[TimeBlock]
    unscheduled: list[Subtask]

    # ─── Final assembly ────────────────────────────────────────────────────
    final_schedule: DaySchedule | None


class AdjustState(TypedDict, total=False):
    """State for the chat-adjust graph (user types a message, we tweak today)."""

    target_date: date
    language: Language
    user_message: str
    current_schedule: DaySchedule

    # Adjustment params produced by chat_agent
    energy_threshold_modifier: float
    remove_blocks_after_hour: int | None
    reschedule_block_title: str | None
    add_task_title: str | None
    add_task_load: str | None
    add_task_minutes: int | None

    # Re-scheduled output
    final_schedule: DaySchedule | None
