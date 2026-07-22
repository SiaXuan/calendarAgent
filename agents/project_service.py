"""
Project service (Phase 4 Step 1).

Business logic for the project layer, kept out of the API routes so it stays
testable: decomposition + plan snapshots, completion tracking (with calendar
history promotion), and progress aggregation. The completion-aware replan lives
here too once the multi-day scheduling model is settled (Step 1.6).
"""
import hashlib
import uuid
from datetime import date, datetime

from agents import calendar_writeback as cw
from agents import plan_import_agent
from agents import task_agent
from api.preferences import get_current_prefs
from integrations import document_parser
from models.project import (
    CompletionRecord, CompletionStatus, PlanSnapshotItem, Project, ProjectStatus,
)
from models.task import CognitiveLoad, Priority, Subtask, Task
from storage import (
    bump_schedule_version, completion_store, project_plan_store, project_store,
    save_completion_store, save_project_plan_store, save_project_store,
    save_task_store, schedule_store, task_store,
)

_CONFIDENCE_FLOOR = 0.55  # below this, treat extraction as "not a plan"

# Fallback rejection text when the LLM flags not-a-plan but gives no reason.
_DEFAULT_REJECTION = {
    "en": "This doesn't look like a plan we can schedule.",
    "zh-CN": "这份内容看起来不像是可以排期的计划。",
    "zh-TW": "這份內容看起來不像是可以排程的計畫。",
    "ja": "これはスケジュールに落とせる計画には見えません。",
}


def subtask_block_key(s: Subtask) -> str:
    """Stable identity mirroring calendar_writeback.block_key / frontend blockKey."""
    return f"{s.parent_id}::{s.title}"


def content_hash(s: Subtask) -> str:
    """Signature of a subtask's schedulable content, for changed-vs-unchanged diff."""
    raw = "|".join([
        s.title,
        str(s.estimated_minutes),
        s.cognitive_load.value,
        s.task_kind.value,
        str(s.suggested_date),
        str(s.phase_label),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def project_tasks(project_id: str) -> list[Task]:
    return [t for t in task_store.values() if t.project_id == project_id]


def _snapshot_item(s: Subtask, project_id: str) -> PlanSnapshotItem:
    bkey = subtask_block_key(s)
    rec = completion_store.get(bkey)
    status = rec.status if rec else CompletionStatus.pending
    return PlanSnapshotItem(
        block_key=bkey, task_id=s.parent_id, title=s.title,
        suggested_date=s.suggested_date, content_hash=content_hash(s), status=status,
    )


async def decompose_project(project_id: str) -> list[Subtask]:
    """
    Rank + decompose a project's tasks into subtasks (single LLM call), stamp
    project_id onto each, and store the plan snapshot. Does NOT write the
    calendar — this is the preview/plan step. Returns the subtasks.
    """
    proj = project_store[project_id]
    tasks = project_tasks(project_id)
    base_date = proj.start_date or date.today()
    language = get_current_prefs().language
    subs = await task_agent.rank_and_decompose(tasks, base_date, language)
    for s in subs:
        s.project_id = project_id
    project_plan_store[project_id] = [_snapshot_item(s, project_id) for s in subs]
    save_project_plan_store()
    return subs


async def import_plan(
    project_id: str,
    *,
    filename: str | None = None,
    data: bytes | None = None,
    text: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Parse a document (pasted text / .txt / .md / .pdf / .docx), extract a plan
    via Claude, and turn its candidate tasks into project Tasks. Import creates
    Tasks only; the reminder change-set is produced separately by replan_project.

    Layered intent gate: document_parser rejects empty/oversized/unsupported
    input (raises DocumentParseError → 422); the LLM then rejects non-plans via
    is_plan/confidence. On `dry_run` nothing is persisted — the caller previews
    the tasks and confirms before a real import.

    Returns {accepted, doc_kind, confidence, ...}. When accepted, includes the
    created (or would-be-created) tasks + project_meta.
    """
    proj = project_store[project_id]
    language = get_current_prefs().language

    if text is not None:
        raw = document_parser.parse_text(text)
    elif data is not None:
        raw = document_parser.parse_upload(filename or "", data)
    else:
        raise document_parser.DocumentParseError(
            "no_input", "Provide a file or text to import.")

    plan = await plan_import_agent.extract_plan(raw, language)

    if not plan.is_plan or plan.confidence < _CONFIDENCE_FLOOR or not plan.candidate_tasks:
        return {
            "accepted": False,
            "project_id": project_id,
            "doc_kind": plan.doc_kind.value,
            "confidence": plan.confidence,
            "reason": plan.rejection_reason
            or _DEFAULT_REJECTION.get(language.value, _DEFAULT_REJECTION["en"]),
        }

    new_tasks = [
        Task(
            id=str(uuid.uuid4()),
            title=c.title,
            description=c.description,
            priority=c.priority or Priority.medium,
            cognitive_load=c.cognitive_load or CognitiveLoad.medium,
            estimated_hours=c.estimated_hours or 1.0,
            deadline=c.explicit_deadline or c.explicit_date,
            source="import",
            project_id=project_id,
        )
        for c in plan.candidate_tasks
    ]

    if not dry_run:
        for t in new_tasks:
            task_store[t.id] = t
        proj.task_ids.extend(t.id for t in new_tasks)
        proj.updated_at = datetime.now()
        save_task_store()
        save_project_store()

    return {
        "accepted": True,
        "dry_run": dry_run,
        "project_id": project_id,
        "doc_kind": plan.doc_kind.value,
        "confidence": plan.confidence,
        "project_meta": plan.project_meta.model_dump(mode="json"),
        "tasks": [t.model_dump(mode="json") for t in new_tasks],
    }


async def replan_project(
    project_id: str, current_reminders: list[dict] | None = None,
) -> dict:
    """
    Completion-aware re-plan (Phase 4). Re-rank + re-decompose the project's
    tasks (single LLM call), diff the new plan against the reminders the frontend
    currently owns, and return a reminder change-set {create, update, delete} for
    the frontend to apply via EventKit — the backend never writes reminders itself.

    Completion-aware: done nodes are left untouched, changed nodes replaced,
    unchanged left in place, dropped nodes deleted. The plan snapshot is refreshed
    so the next replan diffs against this plan. `affected_dates` tells the frontend
    which days to refresh on the daily path (today's blocks re-flow there, not here).
    """
    from agents import reminder_reconcile  # lazy import: breaks the module cycle

    proj = project_store[project_id]
    tasks = project_tasks(project_id)
    base_date = proj.start_date or date.today()
    language = get_current_prefs().language
    new_subs = await task_agent.rank_and_decompose(tasks, base_date, language)
    for s in new_subs:
        s.project_id = project_id

    old_snapshot = project_plan_store.get(project_id, [])
    done_keys = {
        k for k, r in completion_store.items()
        if r.project_id == project_id and r.status == CompletionStatus.done
    }
    changeset = reminder_reconcile.reconcile_reminders(
        new_subs, current_reminders or [], old_snapshot, done_keys)

    project_plan_store[project_id] = [_snapshot_item(s, project_id) for s in new_subs]
    save_project_plan_store()

    return {
        "project_id": project_id,
        "reminders": changeset,
        "affected_dates": reminder_reconcile.affected_dates(changeset, old_snapshot),
    }


def _find_block(target_date: date, bkey: str):
    """Locate a TimeBlock in schedule_store[date] by its logical block_key."""
    sched = schedule_store.get(target_date)
    if sched is None:
        return None
    for b in sched.blocks:
        if cw.block_key(b) == bkey:
            return b
    return None


async def set_block_completion(
    target_date: date, bkey: str, done: bool,
) -> dict:
    """
    Mark a scheduled block done/undone. completion_store is the source of truth
    (Apple Calendar has no done flag). On done, the calendar event is promoted
    to the history namespace so it survives replan; on undo it's demoted back.

    The completion record is always written even if the calendar op fails/isn't
    configured — the record, not the calendar, is authoritative.
    """
    block = _find_block(target_date, bkey)
    project_id = None
    task_id = None
    title = bkey
    if block is not None:
        task = task_store.get(block.task_id) if block.task_id else None
        project_id = task.project_id if task else None
        task_id = block.task_id
        title = block.title

    calendar: dict | None = None
    if done:
        completion_store[bkey] = CompletionRecord(
            block_key=bkey, project_id=project_id, task_id=task_id, title=title,
            scheduled_date=target_date, status=CompletionStatus.done,
            completed_at=datetime.now(),
        )
        if block is not None:
            calendar = await cw.promote_block_to_history(
                target_date, block, datetime.now().isoformat(), project_id)
    else:
        completion_store.pop(bkey, None)
        if block is not None:
            # Demote: drop the history event, restore the active event.
            calendar = await cw.write_block_to_calendar(target_date, block)

    save_completion_store()
    if block is not None:
        block.is_done = done
        bump_schedule_version(target_date)

    return {"block_key": bkey, "done": done, "found_block": block is not None,
            "calendar": calendar}


def set_completion_status(bkey: str, status: CompletionStatus,
                          project_id: str | None = None, title: str = "") -> CompletionRecord:
    """Directly set/clear a completion record (metadata only, no calendar op)."""
    if status == CompletionStatus.pending:
        completion_store.pop(bkey, None)
        save_completion_store()
        return CompletionRecord(block_key=bkey, title=title or bkey,
                                status=CompletionStatus.pending)
    existing = completion_store.get(bkey)
    rec = CompletionRecord(
        block_key=bkey,
        project_id=project_id or (existing.project_id if existing else None),
        task_id=existing.task_id if existing else None,
        title=title or (existing.title if existing else bkey),
        scheduled_date=existing.scheduled_date if existing else None,
        status=status,
        completed_at=datetime.now() if status == CompletionStatus.done else None,
    )
    completion_store[bkey] = rec
    save_completion_store()
    return rec


def project_progress(project_id: str) -> dict:
    """Done/total for a project's last-planned blocks, plus per-day breakdown."""
    snapshot = project_plan_store.get(project_id, [])
    total = len(snapshot)
    done = sum(
        1 for i in snapshot
        if (r := completion_store.get(i.block_key)) and r.status == CompletionStatus.done
    )
    by_day: dict[str, dict] = {}
    for i in snapshot:
        day = str(i.suggested_date) if i.suggested_date else "unscheduled"
        bucket = by_day.setdefault(day, {"total": 0, "done": 0})
        bucket["total"] += 1
        r = completion_store.get(i.block_key)
        if r and r.status == CompletionStatus.done:
            bucket["done"] += 1
    return {"project_id": project_id, "total": total, "done": done, "by_day": by_day}


def completion_heatmap(start: date, end: date) -> dict[str, int]:
    """Count of completed blocks per day in [start, end] — feeds the commit wall."""
    counts: dict[str, int] = {}
    for rec in completion_store.values():
        if rec.status != CompletionStatus.done:
            continue
        d = rec.scheduled_date or (rec.completed_at.date() if rec.completed_at else None)
        if d is None or d < start or d > end:
            continue
        key = str(d)
        counts[key] = counts.get(key, 0) + 1
    return counts


async def delete_project(project_id: str, purge_tasks: bool = True) -> dict:
    """
    Remove a project: delete its calendar events across all days (active +
    history), drop its tasks/snapshot, and forget its completion records.
    """
    calendar = await cw.delete_project_events(project_id)
    proj = project_store.get(project_id)
    if purge_tasks and proj:
        for tid in list(task_store.keys()):
            if task_store[tid].project_id == project_id:
                del task_store[tid]
        from storage import save_task_store
        save_task_store()
    for bkey in [k for k, r in completion_store.items() if r.project_id == project_id]:
        del completion_store[bkey]
    save_completion_store()
    project_plan_store.pop(project_id, None)
    save_project_plan_store()
    project_store.pop(project_id, None)
    save_project_store()
    return {"project_id": project_id, "deleted": True, "calendar": calendar}
