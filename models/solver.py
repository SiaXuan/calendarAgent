"""
Solver I/O models (Phase: 核心闭环 S1).

The multi-day scheduler (agents/solver.py) is the deterministic "muscle" — given
tasks + constraints + energy curves over a horizon, it produces an optimal-ish
placement, OR reports infeasibility WITH relaxations (what would make it fit).

Design note: these are plain Pydantic models, not tied to TimeBlock, so the
solver stays a pure function testable without the rest of the app. The caller
(a tool / node) converts Placement → TimeBlock.
"""
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from models.task import CognitiveLoad, TaskKind


class SolverTask(BaseModel):
    """A unit of work the solver must place. Derived from Subtask + deadline logic."""
    id: str
    title: str
    duration_min: int
    cognitive_load: CognitiveLoad = CognitiveLoad.medium
    task_kind: TaskKind = TaskKind.analytical
    deadline: date | None = None
    # must_do = cannot be deferred past the horizon (near/overdue deadline).
    # The caller decides this; solver treats must_do tasks as hard-to-place.
    must_do: bool = False
    priority: str = "medium"   # high|medium|low — tiebreaker within must_do


class FixedInterval(BaseModel):
    """An immovable block (meeting/meal/pinned) the solver routes around."""
    start: datetime
    end: datetime


class SolverConstraints(BaseModel):
    """Global + per-task constraints the agent translates a request into."""
    work_start_hour: int = 8
    work_end_hour: int = 22
    # Per-task hard windows / inflation, keyed by task id.
    must_be_before: dict[str, date] = Field(default_factory=dict)
    must_be_after: dict[str, date] = Field(default_factory=dict)
    # Low-energy duration inflation: when a day's mean work-hour energy is below
    # `low_energy_threshold`, a task's effective duration is multiplied by this.
    inflate_factor: float = 1.0
    low_energy_threshold: float = 0.45
    buffer_min: int = 10


class Placement(BaseModel):
    task_id: str
    title: str
    day: date
    start: datetime
    end: datetime
    cognitive_load: CognitiveLoad
    task_kind: TaskKind
    energy_at_slot: float
    inflated: bool = False   # True if duration was inflated for low energy


class Relaxation(BaseModel):
    """What WOULD make an infeasible problem fit — drives the agent's clarify dialog."""
    action: str            # human-readable: "extend work hours to 23:00"
    frees_min: int         # how many more task-minutes this would let us place
    cost: str | None = None  # e.g. "misses CV deadline" — None if free lunch


class SolveStatus(str, Enum):
    feasible = "feasible"
    infeasible = "infeasible"


class Bottleneck(BaseModel):
    must_do_min: int
    capacity_min: int
    deficit_min: int
    binding: str   # "work_hours" | "deadline_cluster" | "inflation" | ...


class SolveResult(BaseModel):
    status: SolveStatus
    placements: list[Placement] = Field(default_factory=list)
    # Tasks that couldn't be placed + why (only on infeasible).
    overflow: list[dict] = Field(default_factory=list)
    bottleneck: Bottleneck | None = None
    relaxations: list[Relaxation] = Field(default_factory=list)
