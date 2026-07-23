"""
Multi-day planning (Phase 4, Step 1.6).

A project's plan nodes (imported assignments / milestones, each with an estimated
workload and a deadline) are distributed across the days leading up to their
deadline — a task that needs 8h isn't crammed onto one day; it's split into
sessions over several days, respecting how much free time each day actually has
(work hours minus that day's fixed events) and other commitments.

The distribution is decided by a reasoning LLM (agents/multiday_planner.py) with
a deterministic greedy fallback. The output is a list of dated PlannedChunks; the
daily schedule for a given day picks up that day's chunks and places them into
free windows via the existing energy-aware scheduler.
"""
from datetime import date

from pydantic import BaseModel

from models.task import CognitiveLoad


class DayCapacity(BaseModel):
    """How much schedulable time a given day has — work hours minus that day's
    fixed commitments (meetings / classes). Built from the uploaded calendar; the
    planner never allocates more than this on a day."""
    date: date
    free_minutes: int


class PlannedChunk(BaseModel):
    """One work session the planner placed on a specific day. NOT a blind time
    slice — `title` is the concrete step for that session (what the LLM decided
    the task needs done then, grounded in the source doc), e.g. "读容器与并发章节
    + 记笔记". `task_title` is the parent task it belongs to (for grouping)."""
    project_id: str
    task_id: str
    task_title: str            # parent task, e.g. "作业1：容器与并发"
    title: str                 # the step for this session (meaningful, distinct)
    date: date
    minutes: int
    cognitive_load: CognitiveLoad = CognitiveLoad.deep
