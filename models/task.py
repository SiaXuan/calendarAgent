from datetime import date
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


class Task(BaseModel):
    id: str
    title: str
    description: str | None = None
    priority: Priority
    cognitive_load: CognitiveLoad
    estimated_hours: float
    deadline: date | None = None
    source: str = "manual"   # "manual" | "todoist" | "reminders"
    is_uncertain: bool = False   # triggers ★ planning chat in frontend
    is_instant: bool = False     # quick action (< 10 min), skip decomposition


class Subtask(BaseModel):
    parent_id: str
    title: str
    cognitive_load: CognitiveLoad
    estimated_minutes: int
    suggested_date: date | None = None
    phase_label: str | None = None   # e.g. "Phase 1 · Research"
    is_instant: bool = False         # pass-through quick action, skip scheduling
