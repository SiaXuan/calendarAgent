from datetime import date, datetime
from enum import Enum
from pydantic import BaseModel


class Priority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class CognitiveLoad(str, Enum):
    deep = "deep"       # coding, writing, problem-solving
    medium = "medium"   # emails, planning, light reading
    light = "light"     # admin, exercise, casual review


class TaskKind(str, Enum):
    """
    Orthogonal to CognitiveLoad: indicates *what kind of cognitive process*
    the task needs, which drives optimal time-of-day placement.

    Based on Daniel Pink "When" (2018) and underlying research:
      * Analytical → focused attention, problem-solving (peak alertness time)
      * Insight   → creative, novel-association (off-peak / slightly fatigued)
      * Admin     → procedural, low-focus (afternoon trough is fine)
    """
    analytical = "analytical"
    insight = "insight"
    admin = "admin"


class Task(BaseModel):
    id: str
    title: str
    description: str | None = None
    priority: Priority
    cognitive_load: CognitiveLoad
    task_kind: TaskKind = TaskKind.analytical  # default preserves prior behaviour
    estimated_hours: float
    deadline: date | None = None
    deadline_dt: datetime | None = None  # full due datetime, preserved from reminder
    source: str = "manual"   # "manual" | "todoist" | "reminders"
    is_uncertain: bool = False   # triggers ★ planning chat in frontend
    is_instant: bool = False     # quick action (< 10 min), skip decomposition
    project_id: str | None = None   # groups tasks imported/decomposed together (Phase 4)


class Subtask(BaseModel):
    parent_id: str
    title: str
    cognitive_load: CognitiveLoad
    task_kind: TaskKind = TaskKind.analytical  # inherited from parent or LLM-classified
    estimated_minutes: int
    suggested_date: date | None = None
    deadline: date | None = None          # inherited from parent task
    due_datetime: datetime | None = None  # full reminder due datetime (time preserved)
    phase_label: str | None = None   # e.g. "Phase 1 · Research"
    is_instant: bool = False         # pass-through quick action, skip scheduling
    project_id: str | None = None    # inherited from parent task (Phase 4)
