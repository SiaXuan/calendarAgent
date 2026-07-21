"""
Project layer API (Phase 4 Step 1).

Endpoints to create/manage projects (a batch of tasks imported or decomposed
together), track completion, and feed the dashboard heatmap. The
completion-aware replan endpoint is added in Step 1.6 once the multi-day
scheduling model is settled.
"""
import logging
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agents import project_service as svc
from models.project import CompletionStatus, Project, ProjectStatus
from storage import (
    completion_store, project_plan_store, project_store, save_project_store,
)

router = APIRouter()
_log = logging.getLogger("dayflow")


# ── Project CRUD ──────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    source: str = "manual"
    language: str | None = None
    deadline: date | None = None
    start_date: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    deadline: date | None = None
    start_date: date | None = None
    notes: str | None = None


@router.post("/projects", response_model=Project)
async def create_project(payload: ProjectCreate):
    proj = Project(id=str(uuid.uuid4()), **payload.model_dump())
    project_store[proj.id] = proj
    save_project_store()
    return proj


@router.get("/projects", response_model=list[Project])
async def list_projects():
    return list(project_store.values())


def _require(project_id: str) -> Project:
    proj = project_store.get(project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"No project {project_id}.")
    return proj


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str):
    return _require(project_id)


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, payload: ProjectUpdate):
    proj = _require(project_id)
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(proj, k, v)
    proj.updated_at = datetime.now()
    save_project_store()
    return proj


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, purge_tasks: bool = True):
    _require(project_id)
    return await svc.delete_project(project_id, purge_tasks=purge_tasks)


# ── Plan / progress ───────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/decompose")
async def decompose_project(project_id: str):
    """Rank + decompose the project's tasks and store the plan snapshot (preview,
    no calendar write)."""
    _require(project_id)
    subs = await svc.decompose_project(project_id)
    return {"project_id": project_id, "subtasks": [s.model_dump(mode="json") for s in subs]}


@router.get("/projects/{project_id}/plan")
async def get_project_plan(project_id: str):
    _require(project_id)
    snapshot = project_plan_store.get(project_id, [])
    return {
        "project_id": project_id,
        "items": [
            {
                **item.model_dump(mode="json"),
                "done": bool(
                    (r := completion_store.get(item.block_key))
                    and r.status == CompletionStatus.done
                ),
            }
            for item in snapshot
        ],
    }


@router.get("/projects/{project_id}/progress")
async def get_project_progress(project_id: str):
    _require(project_id)
    return svc.project_progress(project_id)


# ── Completion tracking ───────────────────────────────────────────────────────

class CompleteRequest(BaseModel):
    done: bool = True


@router.post("/schedule/{target_date}/blocks/{block_key:path}/complete")
async def complete_block(target_date: str, block_key: str, payload: CompleteRequest):
    """Mark a scheduled block done/undone (source of truth for the heatmap +
    completion-aware replan). Promotes/demotes the calendar event accordingly."""
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return await svc.set_block_completion(d, block_key, payload.done)


class CompletionSet(BaseModel):
    status: CompletionStatus
    project_id: str | None = None
    title: str = ""


@router.get("/completions")
async def list_completions(project_id: str | None = None):
    records = [
        r.model_dump(mode="json")
        for r in completion_store.values()
        if project_id is None or r.project_id == project_id
    ]
    return {"completions": records}


@router.put("/completions/{block_key:path}")
async def set_completion(block_key: str, payload: CompletionSet):
    rec = svc.set_completion_status(block_key, payload.status, payload.project_id, payload.title)
    return rec.model_dump(mode="json")


@router.get("/completions/heatmap")
async def completion_heatmap(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
):
    """Per-day count of completed blocks in [from, to] — the commit wall.
    Query params: `from` and `to` (YYYY-MM-DD). Defaults to the last 120 days."""
    try:
        end = date.fromisoformat(to) if to else date.today()
        start = date.fromisoformat(from_) if from_ else end - timedelta(days=120)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return {"from": str(start), "to": str(end), "counts": svc.completion_heatmap(start, end)}
