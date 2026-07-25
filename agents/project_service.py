"""
Project service (Phase 4 Step 1).

Business logic for the project layer, kept out of the API routes so it stays
testable: decomposition + plan snapshots, completion tracking (with calendar
history promotion), and progress aggregation. The completion-aware replan lives
here too once the multi-day scheduling model is settled (Step 1.6).
"""
import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta

from agents import calendar_writeback as cw
from agents import plan_import_agent
from agents import task_agent
from api.preferences import get_current_prefs
from integrations import document_parser
from models.project import (
    CompletionRecord, CompletionStatus, PlanSnapshotItem, Project, ProjectStatus,
)
from models.task import CognitiveLoad, Priority, Subtask, Task
from models.planning import PlannedChunk
from storage import (
    bump_schedule_version, completion_store, multiday_plan_store, project_chat_store,
    project_plan_store, project_store, project_task_store, save_completion_store,
    save_multiday_plan_store, save_project_chat_store, save_project_plan_store,
    save_project_store, save_project_task_store, schedule_store, task_store,
)

_log = logging.getLogger("dayflow")

_CONFIDENCE_FLOOR = 0.4   # below this, treat extraction as "not a plan"

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
    return [t for t in project_task_store.values() if t.project_id == project_id]


def _task_as_node(t: Task) -> Subtask:
    """One coarse plan node = the whole task at its stated date, WITHOUT
    decomposition. Import writes these straight to reminders (the syllabus/PRD in
    its original planned form); the fine daily breakdown happens later on the due
    day via the reminder→task→schedule pipeline, grounded by source_excerpt."""
    return Subtask(
        parent_id=t.id,
        title=t.title,
        cognitive_load=t.cognitive_load,
        estimated_minutes=max(25, int(round((t.estimated_hours or 1.0) * 60))),
        suggested_date=t.deadline,
        deadline=t.deadline,
        due_datetime=t.deadline_dt,
        project_id=t.project_id,
    )


def _snapshot_item(s: Subtask, project_id: str) -> PlanSnapshotItem:
    bkey = subtask_block_key(s)
    rec = completion_store.get(bkey)
    status = rec.status if rec else CompletionStatus.pending
    return PlanSnapshotItem(
        block_key=bkey, task_id=s.parent_id, title=s.title,
        suggested_date=s.suggested_date, content_hash=content_hash(s), status=status,
    )


def get_or_build_plan(project_id: str) -> list[PlanSnapshotItem]:
    """
    The project's plan snapshot. If none is stored yet but the project already
    has tasks (e.g. imported before snapshots were written), build the coarse
    node-per-task snapshot on the fly and persist it — so "计划节点" is never
    empty for a project that has tasks.
    """
    snapshot = project_plan_store.get(project_id)
    if snapshot:
        return snapshot
    tasks = project_tasks(project_id)
    if not tasks:
        return []
    snapshot = [_snapshot_item(_task_as_node(t), project_id) for t in tasks]
    project_plan_store[project_id] = snapshot
    save_project_plan_store()
    return snapshot


# ── Multi-day planning (Step 1.6) ─────────────────────────────────────────────

_MEAL_BUFFER_MIN = 90         # rough lunch+dinner carve-out from a day's work window


def _in_window_project_nodes(today: date) -> list[Task]:
    """Project nodes eligible for scheduling NOW — not done, and with a deadline
    inside the scheduling window. Far-deadline nodes stay off the radar until
    their deadline approaches; undated nodes aren't scheduled (no deadline pull)."""
    from agents.nodes import SCHEDULE_HORIZON_DAYS
    window_end = today + timedelta(days=SCHEDULE_HORIZON_DAYS)
    out: list[Task] = []
    for t in project_task_store.values():
        if not t.deadline or t.deadline > window_end:
            continue
        rec = completion_store.get(f"{t.id}::{t.title}")
        if rec and rec.status == CompletionStatus.done:
            continue
        out.append(t)
    return out


def _chunk_block_key(c: PlannedChunk) -> str:
    """A chunk's stable completion identity — mirrors calendar_writeback.block_key
    once it becomes a Subtask/TimeBlock ("{task_id}::{title}"). This is what
    completion_store keys on, so a chunk's done-ness survives across days."""
    return f"{c.task_id}::{c.title}"


def _chunk_done(c: PlannedChunk) -> bool:
    rec = completion_store.get(_chunk_block_key(c))
    return bool(rec and rec.status == CompletionStatus.done)


def _prune_stale_chunks() -> bool:
    """Drop chunks whose task no longer exists, or whose task's chunks are ALL
    done (nothing left to schedule/carry). Returns True if anything changed."""
    changed = False
    for pid in list(multiday_plan_store.keys()):
        kept = [
            c for c in multiday_plan_store[pid]
            if c.task_id in project_task_store and not _chunk_done(c)
        ]
        if len(kept) != len(multiday_plan_store[pid]):
            changed = True
        if kept:
            multiday_plan_store[pid] = kept
        else:
            del multiday_plan_store[pid]
    return changed


async def ensure_multiday_plan(
    anchor_date: date,
    fixed_minutes_by_date: dict[date, int] | None = None,
    *,
    force: bool = False,
) -> dict:
    """Incrementally keep the multi-day plan current, called before generating a
    day's schedule. Only project nodes that have NEWLY entered the scheduling
    window (in-window but not yet planned) get the LLM planner; already-planned
    projects are left untouched — their unfinished chunks carry forward via
    `chunk_subtasks_for_date`. This is the automatic trigger that replaced the
    old manual "plan multi-day" button.

    `anchor_date` is the day being generated (the window front); "a new day
    arrived" simply means the window rolled forward and more nodes fell in.
    `fixed_minutes_by_date` is the committed time per day the frontend read from
    the local calendar (absent → future days treated as fully free).
    `force=True` replans ALL in-window nodes from scratch (manual override /
    dev backdoor); it clears their existing chunks first.
    """
    from agents import multiday_planner
    from agents.nodes import SCHEDULE_HORIZON_DAYS

    changed = _prune_stale_chunks()

    in_window = _in_window_project_nodes(anchor_date)
    if force:
        for t in in_window:
            multiday_plan_store.pop(t.id, None)

    planned_tasks = {c.task_id for cs in multiday_plan_store.values() for c in cs}
    new_nodes = [t for t in in_window if t.id not in planned_tasks]

    if not new_nodes:
        if changed:
            save_multiday_plan_store()
        return {"planned": 0, "new_nodes": 0, "by_project": {}}

    prefs = get_current_prefs()
    work_minutes = max(0, (prefs.work_end - prefs.work_start) * 60 - _MEAL_BUFFER_MIN)
    horizon_end = anchor_date + timedelta(days=SCHEDULE_HORIZON_DAYS)

    # Subtract time already committed to previously-planned chunks so the new
    # nodes only take each day's REMAINING capacity (no double-booking).
    effective_fixed: dict[date, int] = dict(fixed_minutes_by_date or {})
    for cs in multiday_plan_store.values():
        for c in cs:
            effective_fixed[c.date] = effective_fixed.get(c.date, 0) + c.minutes
    capacities = multiday_planner.build_capacities(
        anchor_date, horizon_end, work_minutes, effective_fixed)

    chunks = await multiday_planner.plan_project_work(
        new_nodes, capacities, anchor_date, prefs.language)

    by_project: dict[str, list[PlannedChunk]] = {}
    for c in chunks:
        multiday_plan_store.setdefault(c.project_id, []).append(c)
        by_project.setdefault(c.project_id, []).append(c)
    save_multiday_plan_store()

    return {
        "planned": len(chunks),
        "new_nodes": len(new_nodes),
        "by_project": {pid: len(cs) for pid, cs in by_project.items()},
    }


def chunk_subtasks_for_date(target_date: date) -> list[Subtask]:
    """The planned project work sessions to place on `target_date`, as Subtasks —
    both the chunks scheduled FOR that day and any past-day chunks left unfinished
    (carried forward, marked `carried_over` so the day shows "继续昨天没做完的 X").
    A chunk marked done in completion_store is dropped; its title is kept verbatim
    so the carried block_key matches the original and completing it collapses the
    carry."""
    out: list[Subtask] = []
    for chunks in multiday_plan_store.values():
        for c in chunks:
            if c.date > target_date:      # future day handles it when it arrives
                continue
            if _chunk_done(c):            # already finished — nothing to schedule
                continue
            carried = c.date < target_date
            parent = project_task_store.get(c.task_id)
            out.append(Subtask(
                parent_id=c.task_id, title=c.title, cognitive_load=c.cognitive_load,
                estimated_minutes=c.minutes, suggested_date=target_date,
                deadline=parent.deadline if parent else None,
                phase_label=c.task_title, project_id=c.project_id,
                carried_over=carried))
    return out


# ── Per-project planning conversation ─────────────────────────────────────────

def _plan_context(project_id: str) -> str:
    import json
    rows = [
        {
            "title": t.title,
            "estimated_hours": t.estimated_hours,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "context": t.source_excerpt or t.description,
        }
        for t in project_tasks(project_id)
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2) if rows else "(还没有任务)"


def _apply_task_revision(project_id: str, candidates) -> None:
    """Replace the project's tasks with the chat's revised set, preserving a
    task's id when its title is unchanged (so completion/reminders survive), then
    rebuild the plan snapshot."""
    proj = project_store[project_id]
    existing = {t.title: t for t in project_tasks(project_id)}
    kept: dict[str, Task] = {}
    for c in candidates:
        deadline = c.explicit_deadline or c.explicit_date
        if c.title in existing:
            t = existing[c.title]
            if c.description is not None:
                t.description = c.description
            if c.estimated_hours:
                t.estimated_hours = c.estimated_hours
            if deadline:
                t.deadline = deadline
            if c.priority:
                t.priority = c.priority
            if c.cognitive_load:
                t.cognitive_load = c.cognitive_load
            if c.source_excerpt:
                t.source_excerpt = c.source_excerpt
            kept[t.id] = t
        else:
            t = Task(
                id=str(uuid.uuid4()), title=c.title, description=c.description,
                priority=c.priority or Priority.medium,
                cognitive_load=c.cognitive_load or CognitiveLoad.medium,
                estimated_hours=c.estimated_hours or 1.0, deadline=deadline,
                source="chat", project_id=project_id, source_excerpt=c.source_excerpt)
            kept[t.id] = t
    for t in list(project_task_store.values()):
        if t.project_id == project_id and t.id not in kept:
            del project_task_store[t.id]
    for t in kept.values():
        project_task_store[t.id] = t
    proj.task_ids = list(kept.keys())
    proj.updated_at = datetime.now()
    save_project_task_store()
    save_project_store()
    project_plan_store.pop(project_id, None)   # rebuilt below from the new tasks
    get_or_build_plan(project_id)


async def chat_about_plan(
    project_id: str, message: str,
    doc_text: str | None = None, image: tuple[bytes, str] | None = None,
) -> dict:
    """One turn of the project's planning conversation. The turn may carry an
    attached document's text or an image (pasting a syllabus IS just a message).
    The LLM replies and, when the content describes schedulable work or the user
    asks for a change, revises the plan — but never hard-rejects; unclear input
    gets a clarifying reply. Persists both turns. Returns {reply, plan_changed}."""
    from agents import project_chat

    proj = project_store[project_id]
    language = get_current_prefs().language
    history = project_chat_store.get(project_id, [])
    result = await project_chat.converse(
        proj.name, _plan_context(project_id), history, message, language,
        doc_text=doc_text, image=image)

    changed = result.tasks is not None
    if changed:
        _apply_task_revision(project_id, result.tasks)

    progress_applied = 0
    if result.progress:
        progress_applied = _apply_chat_progress(project_id, result.progress)

    # What to store as the user's turn — the typed text, or a note when it was
    # attachment-only (the doc/image bytes themselves aren't kept in history).
    user_turn = message.strip()
    if not user_turn:
        user_turn = "（发来一张图片）" if image is not None else "（发来一份文档）"
    project_chat_store[project_id] = history + [
        {"role": "user", "content": user_turn},
        {"role": "assistant", "content": result.reply},
    ]
    save_project_chat_store()
    return {"reply": result.reply, "plan_changed": changed,
            "progress_applied": progress_applied}


def _apply_chat_progress(project_id: str, progress) -> int:
    """Reflect a chat-reported progress update onto the multi-day plan's chunks.
    `done` marks every chunk of the matching task complete (so it stops being
    scheduled/carried and counts on the heatmap); `in_progress` clears any done
    record so it keeps flowing. Matches tasks by title within this project.
    Returns the number of chunks touched."""
    title_to_task = {t.title: t for t in project_tasks(project_id)}
    touched = 0
    for p in progress:
        task = title_to_task.get(p.task_title)
        if not task:
            continue
        chunks = [c for c in multiday_plan_store.get(project_id, [])
                  if c.task_id == task.id]
        for c in chunks:
            bkey = _chunk_block_key(c)
            if p.status == "done":
                completion_store[bkey] = CompletionRecord(
                    block_key=bkey, project_id=project_id, task_id=task.id,
                    title=c.title, scheduled_date=c.date,
                    status=CompletionStatus.done, completed_at=datetime.now())
            else:  # in_progress — make sure it isn't stuck marked done
                completion_store.pop(bkey, None)
            touched += 1
    if touched:
        save_completion_store()
    return touched


def record_chat_turn(project_id: str, role: str, content: str) -> None:
    """Append one turn to the project conversation — e.g. an import, so the chat
    reflects what happened outside a typed chat turn."""
    turns = project_chat_store.get(project_id, [])
    turns.append({"role": role, "content": content})
    project_chat_store[project_id] = turns
    save_project_chat_store()


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
    instruction: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Parse a document (pasted text / .txt / .md / .pdf / .docx), extract a plan
    via Claude, and turn its candidate tasks into project Tasks.

    New-flow (Phase 4): a confirmed import writes the plan directly. Each task
    becomes ONE coarse plan node at its stated date — the syllabus/PRD kept in
    its original planned form, NOT decomposed. We write the plan snapshot (so the
    nodes show immediately) and diff the coarse nodes against the reminders the
    frontend currently owns (`current_reminders`), returning a reminder
    change-set the frontend applies via EventKit. Fine daily breakdown happens
    later on each node's due day, grounded by the stored source_excerpt.

    Layered intent gate: document_parser rejects empty/oversized/unsupported
    input (raises DocumentParseError → 422); the LLM then rejects non-plans via
    is_plan/confidence. On `dry_run` nothing is persisted and no reminders are
    produced — the caller previews the tasks and confirms before a real import.

    Returns {accepted, doc_kind, confidence, ...}. When accepted, includes the
    created (or would-be-created) tasks + project_meta; a confirmed import also
    includes the reminder change-set + affected_dates.
    """
    proj = project_store[project_id]
    language = get_current_prefs().language

    # Image upload (pasted screenshot / photo) → Claude vision, no text parse.
    image_mime = document_parser.image_mime(filename or "") if data is not None else None
    if image_mime:
        document_parser.check_size(data)
        raw = "(image)"
        plan = await plan_import_agent.extract_plan_from_image(
            data, image_mime, language, instruction=instruction)
    else:
        if text is not None:
            raw = document_parser.parse_text(text)
        elif data is not None:
            raw = document_parser.parse_upload(filename or "", data)
        else:
            raise document_parser.DocumentParseError(
                "no_input", "Provide a file or text to import.")
        plan = await plan_import_agent.extract_plan(raw, language, instruction=instruction)

    _log.info(
        "import_plan[%s]: %d chars → is_plan=%s conf=%.2f kind=%s tasks=%d | preview=%r",
        project_id, len(raw), plan.is_plan, plan.confidence,
        plan.doc_kind.value, len(plan.candidate_tasks), raw[:300],
    )

    # Trust the extraction: if it pulled out schedulable items with non-trivial
    # confidence, accept — even if the model's own `is_plan` meta-flag was
    # conservative (a bare TOC / reading list the user wants to get through is a
    # plan). Reject only when there's genuinely nothing to schedule.
    if not plan.candidate_tasks or plan.confidence < _CONFIDENCE_FLOOR:
        return {
            "accepted": False,
            "project_id": project_id,
            "doc_kind": plan.doc_kind.value,
            "confidence": plan.confidence,
            "reason": plan.rejection_reason
            or _DEFAULT_REJECTION.get(language.value, _DEFAULT_REJECTION["en"]),
        }

    # Deterministic date shift (e.g. an old syllabus moved to a new term). The LLM
    # only parsed the instruction into plan.adjustment; the calendar math is here.
    from agents import plan_reschedule
    candidates = plan_reschedule.apply_adjustment(plan.candidate_tasks, plan.adjustment)

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
            source_excerpt=c.source_excerpt,
        )
        for c in candidates
    ]

    result = {
        "accepted": True,
        "dry_run": dry_run,
        "project_id": project_id,
        "doc_kind": plan.doc_kind.value,
        "confidence": plan.confidence,
        "project_meta": plan.project_meta.model_dump(mode="json"),
        "tasks": [t.model_dump(mode="json") for t in new_tasks],
    }
    if dry_run:
        return result

    # Project tasks go into the project-scoped store, NOT the global scheduling
    # task_store — they're unconfirmed plan nodes and must not be auto-scheduled
    # on the daily path.
    for t in new_tasks:
        project_task_store[t.id] = t
    proj.task_ids.extend(t.id for t in new_tasks)
    proj.updated_at = datetime.now()
    save_project_task_store()
    save_project_store()

    # Write the plan snapshot straight from the import so "计划节点" fills
    # immediately for review. Reminders are NOT written here — the user reviews
    # the snapshot on the project page and then explicitly writes it to the
    # calendar via POST /projects/{id}/replan (which returns the change-set the
    # frontend applies via EventKit).
    coarse = [_task_as_node(t) for t in project_tasks(project_id)]
    project_plan_store[project_id] = [_snapshot_item(s, project_id) for s in coarse]
    save_project_plan_store()

    titles = "、".join(t.title for t in new_tasks[:6])
    more = "…" if len(new_tasks) > 6 else ""
    record_chat_turn(
        project_id, "assistant",
        f"已整理出 {len(new_tasks)} 个计划节点：{titles}{more}。要调整就直接说。")
    return result


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

    Nodes are COARSE — one per task at its stated date, matching import. Reminders
    are the plan in its original form; the fine daily breakdown happens on each
    node's due day (grounded by source_excerpt), not here. So a replan is a cheap,
    LLM-free re-sync after tasks/completions change.
    """
    from agents import reminder_reconcile  # lazy import: breaks the module cycle

    tasks = project_tasks(project_id)
    new_subs = [_task_as_node(t) for t in tasks]

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
        for tid in list(project_task_store.keys()):
            if project_task_store[tid].project_id == project_id:
                del project_task_store[tid]
        save_project_task_store()
    for bkey in [k for k, r in completion_store.items() if r.project_id == project_id]:
        del completion_store[bkey]
    save_completion_store()
    project_plan_store.pop(project_id, None)
    save_project_plan_store()
    if multiday_plan_store.pop(project_id, None) is not None:
        save_multiday_plan_store()
    if project_chat_store.pop(project_id, None) is not None:
        save_project_chat_store()
    project_store.pop(project_id, None)
    save_project_store()
    return {"project_id": project_id, "deleted": True, "calendar": calendar}
