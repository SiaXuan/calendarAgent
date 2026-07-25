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

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from agents import project_service as svc
from integrations import document_parser
from integrations.document_parser import DocumentParseError
from models.project import CompletionStatus, Project, ProjectStatus
from storage import (
    completion_store, multiday_plan_store, project_chat_store, project_store,
    save_project_store,
)

router = APIRouter()
_log = logging.getLogger("dayflow")


# ── Project CRUD ──────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    source: str = "manual"
    language: str | None = None
    color: str | None = None
    deadline: date | None = None
    start_date: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    color: str | None = None
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
    snapshot = svc.get_or_build_plan(project_id)
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


@router.post("/projects/{project_id}/import")
async def import_plan(
    project_id: str,
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    instruction: str | None = Form(None),
    dry_run: bool = Form(False),
):
    """
    Import a plan document (pasted text / .txt / .md / .pdf / .docx) into the
    project. Multipart form: exactly one of `file` or `text`, an optional
    natural-language `instruction` (e.g. reuse an old syllabus in a new term),
    and optional `dry_run` (preview without persisting).

    A confirmed import creates the project's Tasks and writes the plan snapshot
    (so "计划节点" fills for review). Reminders are written separately: the user
    reviews the snapshot and then confirms via POST /projects/{id}/replan. 422 on
    unparseable input or a non-plan doc.
    """
    _require(project_id)
    if (file is None) == (text is None):
        raise HTTPException(
            status_code=422, detail="Provide exactly one of `file` or `text`.")
    try:
        if file is not None:
            result = await svc.import_plan(
                project_id, filename=file.filename, data=await file.read(),
                instruction=instruction, dry_run=dry_run)
        else:
            result = await svc.import_plan(
                project_id, text=text, instruction=instruction, dry_run=dry_run)
    except DocumentParseError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message})
    except Exception:
        # The extraction LLM can fail or return a shape we can't validate. Log the
        # real traceback but degrade to a clean 502 instead of a raw 500.
        _log.exception("Plan import failed for project %s", project_id)
        raise HTTPException(
            status_code=502,
            detail={"code": "extraction_failed",
                    "message": "无法解析这份计划，请重试或换一种格式/内容。"})
    if not result.get("accepted", False):
        raise HTTPException(status_code=422, detail=result)
    return result


class CurrentReminder(BaseModel):
    """One reminder the frontend currently owns for this project (read via
    EventKit; identified by its per-node tag in the reminder notes)."""
    tag_key: str
    title: str | None = None
    due: str | None = None


class ReplanRequest(BaseModel):
    current_reminders: list[CurrentReminder] = []


@router.post("/projects/{project_id}/replan")
async def replan_project(project_id: str, payload: ReplanRequest):
    """
    Completion-aware re-plan (see docs/ARCHITECTURE.md §0). Re-decomposes the
    project and returns a reminder change-set {create, update, delete} for the
    frontend to apply via EventKit; the backend never writes reminders. Done
    nodes are left untouched. `affected_dates` lists days the frontend should
    refresh on the daily path (today's time blocks re-flow there, not here).
    """
    _require(project_id)
    return await svc.replan_project(
        project_id, [r.model_dump() for r in payload.current_reminders])


# ── Multi-day planning (Step 1.6) ─────────────────────────────────────────────

class MultidayPlanRequest(BaseModel):
    """Committed minutes per day (YYYY-MM-DD → minutes) the frontend read from the
    local calendar, subtracted from each day's work window. Empty → every day is
    treated as a full work window."""
    fixed_minutes_by_date: dict[str, int] = {}


@router.post("/projects/plan-multiday")
async def plan_multiday(payload: MultidayPlanRequest = MultidayPlanRequest()):
    """Force a full multi-day replan of all in-window project nodes (manual
    override / dev backdoor). The normal path is automatic: each daily generate
    calls `ensure_multiday_plan`, which incrementally picks up newly-in-window
    nodes. Kept so a just-edited outline can be redistributed immediately."""
    fixed: dict[date, int] = {}
    for k, v in payload.fixed_minutes_by_date.items():
        try:
            fixed[date.fromisoformat(k)] = int(v)
        except (ValueError, TypeError):
            continue
    return await svc.ensure_multiday_plan(date.today(), fixed or None, force=True)


@router.get("/projects/{project_id}/multiday")
async def get_multiday_plan(project_id: str):
    """The project's distributed work sessions (per-day chunks), grouped by date —
    for showing the plan on the project page."""
    _require(project_id)
    by_date: dict[str, list[dict]] = {}
    for c in multiday_plan_store.get(project_id, []):
        by_date.setdefault(str(c.date), []).append({
            "title": c.title, "task_title": c.task_title,
            "minutes": c.minutes, "task_id": c.task_id})
    return {"project_id": project_id, "by_date": dict(sorted(by_date.items()))}


# ── Per-project planning conversation ─────────────────────────────────────────

@router.get("/projects/{project_id}/chat")
async def get_project_chat(project_id: str):
    """The project's full conversation history [{role, content}]."""
    _require(project_id)
    return {"project_id": project_id, "messages": project_chat_store.get(project_id, [])}


@router.post("/projects/{project_id}/chat")
async def post_project_chat(
    project_id: str,
    message: str = Form(""),
    file: UploadFile | None = File(None),
):
    """One planning turn (multipart). `message` plus an optional attached `file`
    (image → vision; .txt/.md/.pdf/.docx → parsed to text). Pasting a syllabus is
    just a message here — the conversational LLM decides what to do; there's no
    hard intent gate. Returns {reply, plan_changed}; the frontend refetches the
    plan."""
    _require(project_id)
    msg = message.strip()
    doc_text = None
    image = None
    if file is not None:
        data = await file.read()
        try:
            mime = document_parser.image_mime(file.filename or "")
            if mime:
                document_parser.check_size(data)
                image = (data, mime)
            else:
                doc_text = document_parser.parse_upload(file.filename or "", data)
        except DocumentParseError as e:
            raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message})
    if not msg and file is None:
        raise HTTPException(status_code=422, detail="Empty message.")
    try:
        return await svc.chat_about_plan(project_id, msg, doc_text=doc_text, image=image)
    except Exception:
        _log.exception("Plan chat failed for project %s", project_id)
        raise HTTPException(status_code=502, detail={
            "code": "chat_failed", "message": "对话出错了，请重试。"})


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
