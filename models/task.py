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


class Subtask(BaseModel):
    parent_id: str
    title: str
    cognitive_load: CognitiveLoad
    estimated_minutes: int
    suggested_date: date | None = None
