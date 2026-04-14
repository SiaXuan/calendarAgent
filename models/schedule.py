from datetime import date, datetime
from enum import Enum
from pydantic import BaseModel

from models.task import CognitiveLoad, Subtask


class BlockType(str, Enum):
    fixed = "fixed"           # meetings, classes — do not move
    meal = "meal"             # protected meal break (lunch / dinner)
    suggested = "suggested"   # agent recommends but user hasn't confirmed (soft-fallback slot)
    scheduled = "scheduled"   # agent-assigned task block (user confirmed or high-confidence)
    free = "free"
    instant = "instant"       # quick reminders (< 10 min), shown as pass-through


class TimeBlock(BaseModel):
    start: datetime
    end: datetime
    block_type: BlockType
    task_id: str | None = None
    title: str
    cognitive_load: CognitiveLoad | None = None
    notes: str | None = None
    phase_label: str | None = None   # e.g. "Phase 1 · Research"
    focus_minutes: int = 25          # Pomodoro focus duration
    break_minutes: int = 5           # break between Pomodoros
    pomodoro_count: int = 1          # number of focus sessions
    deadline: date | None = None       # inherited from parent task, drives urgency color
    is_uncertain: bool = False         # ★ flag — task scope unclear
    has_explicit_time: bool = True     # False = reminder has date only, no specific time


class FreeWindow(BaseModel):
    start_hour: int
    start_minute: int = 0
    end_hour: int
    end_minute: int = 0
    duration_minutes: int
    energy_score: float = 0.0   # avg energy in this window, 0.0–1.0


class DaySchedule(BaseModel):
    date: date
    energy_curve: list[float]      # 24 values, index = hour (0 = midnight)
    blocks: list[TimeBlock]
    unscheduled: list[Subtask]     # tasks that didn't fit today
    health_summary: str


class ScheduleResult(BaseModel):
    """Internal result returned by the Scheduler Agent."""
    blocks: list[TimeBlock]
    unscheduled: list[Subtask]
