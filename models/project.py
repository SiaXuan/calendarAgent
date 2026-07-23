"""
Project layer (Phase 4 Step 1).

A Project groups a batch of tasks that were imported or decomposed together, so
they can be managed as a unit — viewed, progress-tracked, batch re-planned, and
batch-deleted across days. Before this, agent-written events were only
manageable per-date; a plan change left earlier writes orphaned.

Storage is in-memory + JSON in Phase 1-4 (see storage.py). On a future cloud
migration, `completion_store` should key on `block_key` (unique index) and
`project_plan_store` can be a JSON blob per project.
"""
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    active = "active"
    archived = "archived"
    done = "done"


class CompletionStatus(str, Enum):
    pending = "pending"
    done = "done"
    skipped = "skipped"


class Project(BaseModel):
    id: str
    name: str
    description: str | None = None
    source: str = "manual"          # "manual" | "import" | "chat"
    language: str | None = None     # locale the project text was produced in
    color: str | None = None        # user-chosen hex (e.g. "#FF9500"); the
                                    # per-project reminder list is tinted with it
    status: ProjectStatus = ProjectStatus.active
    deadline: date | None = None
    start_date: date | None = None
    task_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CompletionRecord(BaseModel):
    """
    Whether a scheduled block was actually done, keyed by the stable
    block_key ("{parent_id}::{title}"). This is the source of truth Apple
    Calendar lacks — used for review, the heatmap, and completion-aware replan.
    """
    block_key: str
    project_id: str | None = None
    task_id: str | None = None
    title: str
    scheduled_date: date | None = None
    status: CompletionStatus = CompletionStatus.done
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.now)


class PlanSnapshotItem(BaseModel):
    """
    One decomposed block from a project's last written plan. `content_hash`
    lets replan tell changed vs unchanged blocks apart cheaply.
    """
    block_key: str
    task_id: str
    title: str
    suggested_date: date | None = None
    content_hash: str
    status: CompletionStatus = CompletionStatus.pending
