"""
Multi-format plan import — intermediate representation (Phase 4 Step 2).

A document (syllabus / PRD / schedule table / freeform goal, as pasted text,
PDF, or DOCX) is parsed to plain text, then Claude extracts a structured
`ExtractedPlan`. Candidate tasks then become project Tasks (see
agents/plan_import_agent.py + api/projects.py::import_plan); the reminder
change-set is produced separately by the replan path.
"""
import json
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from models.task import CognitiveLoad, Priority


class DocKind(str, Enum):
    syllabus = "syllabus"
    prd = "prd"
    schedule_table = "schedule_table"
    roadmap = "roadmap"
    freeform_goal = "freeform_goal"
    other = "other"


class CandidateTask(BaseModel):
    """One task-like item the LLM pulled out of the document, before it becomes a
    real Task. Optional scheduling hints are honoured downstream when present."""
    title: str
    description: str | None = None
    explicit_date: date | None = None       # a concrete date stated in the doc
    explicit_deadline: date | None = None
    estimated_hours: float | None = None
    priority: Priority | None = None
    cognitive_load: CognitiveLoad | None = None
    phase_label: str | None = None
    needs_decomposition: bool = True         # false only for a single-action item
    source_excerpt: str | None = None        # the doc snippet it came from


class ProjectMeta(BaseModel):
    """Project-level fields the doc implies — offered to the frontend to fill in
    the project's deadline / start_date (relative dates resolve against these)."""
    title: str | None = None
    description: str | None = None
    deadline: date | None = None
    start_date: date | None = None


class ExtractedPlan(BaseModel):
    """Structured output of the extraction LLM call."""
    is_plan: bool
    doc_kind: DocKind = DocKind.other
    confidence: float = 0.0                  # 0..1; gate rejects below 0.55
    rejection_reason: str | None = None      # localized, shown when not a plan
    has_explicit_schedule: bool = False
    project_meta: ProjectMeta = Field(default_factory=ProjectMeta)
    candidate_tasks: list[CandidateTask] = Field(default_factory=list)

    @field_validator("candidate_tasks", "project_meta", mode="before")
    @classmethod
    def _coerce_json_string(cls, v):
        """Claude's structured output sometimes serialises a nested list/object
        as a JSON *string* instead of a native value; decode it before validation
        so the whole extraction doesn't fail (was surfacing as a 500 on import)."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return v
        return v
