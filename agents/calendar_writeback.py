"""
CalDAV write-back helpers.

Lifted out of agents/orchestrator.py during Phase A migration so the
orchestrator can be deleted once the graph-based path is fully wired.
These functions are not LangGraph nodes — they're triggered by explicit
"sync to calendar" buttons in the UI, not by the schedule generation flow.
"""
import asyncio
import hashlib
from datetime import date, datetime

from agents import nodes  # for _calendar_cache invalidation
from integrations.caldav_client import AGENT_DESC_TAG
from models.schedule import BlockType, TimeBlock
from storage import completion_store, schedule_store, task_store
from models.project import CompletionStatus

# Bracket-less prefix shared by the bare tag and every per-block tag.
# IMPORTANT: the bare AGENT_DESC_TAG "[agent-scheduled:dayflow]" is NOT a
# substring of a per-block tag "[agent-scheduled:dayflow:KEY]" (the trailing
# "]" breaks it), so full-day cleanup must match on this PREFIX instead —
# otherwise re-syncing leaves the previous events behind (duplicates).
_AGENT_TAG_PREFIX = AGENT_DESC_TAG[:-1]  # "[agent-scheduled:dayflow"

# Completed blocks are re-written into a separate tag namespace so they survive
# the active-block cleanup (which only matches _AGENT_TAG_PREFIX). This is what
# makes completion-aware replan cheap: history is naturally immune to deletion.
_AGENT_HISTORY_PREFIX = "[agent-history:dayflow"

# Project namespace tag → enables cross-day batch delete/manage per project.
_AGENT_PROJECT_PREFIX = "[agent-project:"


def block_key(block: TimeBlock) -> str:
    """
    Stable logical identity of a block within a day: "{task_id}::{title}".
    Mirrors the frontend blockKey() and agents.nodes._subtask_block_key, so
    completion_store / pins / reconcile all key on the same string.
    """
    base = block.task_id or f"block-{block.start.isoformat()}"
    return f"{base}::{block.title}"


def _tag_key(key: str) -> str:
    """
    Short deterministic hash of the logical block_key, used INSIDE the CalDAV
    tag. Hashing avoids titles that contain "]" or ":" breaking the tag's
    substring matching (block_key is human/title-derived).
    """
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _block_tag(block: TimeBlock) -> str:
    """Per-block active agent tag: "[agent-scheduled:dayflow:<hash>]"."""
    return f"{_AGENT_TAG_PREFIX}:{_tag_key(block_key(block))}]"


def _history_tag(block: TimeBlock) -> str:
    """Per-block history tag: "[agent-history:dayflow:<hash>]"."""
    return f"{_AGENT_HISTORY_PREFIX}:{_tag_key(block_key(block))}]"


def _project_tag(project_id: str) -> str:
    return f"{_AGENT_PROJECT_PREFIX}{project_id}]"


def _project_id_for_block(block: TimeBlock) -> str | None:
    """Resolve the block's owning project via its parent task."""
    if not block.task_id:
        return None
    task = task_store.get(block.task_id)
    return task.project_id if task else None


def _active_tag_str(block: TimeBlock) -> str:
    """Full DESCRIPTION tag block for an active event: block tag + project tag."""
    lines = [_block_tag(block)]
    pid = _project_id_for_block(block)
    if pid:
        lines.append(_project_tag(pid))
    return "\n".join(lines)


def _is_done(block: TimeBlock) -> bool:
    rec = completion_store.get(block_key(block))
    return rec is not None and rec.status == CompletionStatus.done


def _block_description(block: TimeBlock) -> str:
    parts: list[str] = []
    if block.cognitive_load:
        parts.append(f"Cognitive load: {block.cognitive_load.value}")
    if block.phase_label:
        parts.append(block.phase_label)
    if block.notes:
        parts.append(block.notes)
    return "\n".join(parts)


def _tag_for_key(key: str) -> str:
    """Per-block tag for a block key. Empty key = the legacy bare full-day tag."""
    if not key:
        return AGENT_DESC_TAG  # keyless legacy events written before per-block tags
    return f"{_AGENT_TAG_PREFIX}:{key}]"


def _unchanged(existing: dict, block: TimeBlock) -> bool:
    """
    True if the calendar event already matches the desired block, so it can be
    left in place (stable UID, no churn). Matched on title + start + end; this is
    duplicate-safe either way — a false "changed" only re-writes, never duplicates
    (we always delete-before-write for a key that already exists).
    """
    return (
        existing["title"] == block.title
        and existing["start"] == block.start
        and existing["end"] == block.end
    )


async def write_schedule_to_calendar(target_date: date) -> dict:
    """
    Reconcile schedule_store[target_date] into iCloud Calendar via a block-level
    diff (Step 0 hardening). Instead of wiping the whole day and rewriting, we:
      - leave UNCHANGED blocks in place (stable UID, no churn),
      - DELETE-then-WRITE changed blocks (delete must succeed first, else abort
        that block and report — never write a duplicate on a failed delete),
      - WRITE added blocks, DELETE removed/stale ones.
    Only `scheduled` and `suggested` blocks are written.

    Returns {written, deleted, unchanged, failed:[{block_key, op, reason}], ok}.
    `ok` is False (and `error` set) when the whole operation could not proceed
    (e.g. CalDAV not configured / calendar unreadable) or any block failed.
    """
    schedule = schedule_store.get(target_date)
    if schedule is None:
        return {"written": 0, "deleted": 0, "unchanged": 0, "failed": [], "ok": True}

    # Skip completed blocks — they live as history events (written by
    # promote_block_to_history at completion time) and must NOT be re-added as
    # active, or reconcile would duplicate them alongside their history copy.
    writable = [
        b for b in schedule.blocks
        if b.block_type in (BlockType.scheduled, BlockType.suggested) and not _is_done(b)
    ]

    def _do_write() -> dict:
        from integrations.caldav_client import (
            write_event, delete_events_with_tag, fetch_agent_events,
            CalDAVError, CalDAVNotConfigured,
        )
        result: dict = {"written": 0, "deleted": 0, "unchanged": 0, "failed": []}

        # 1. Read active agent events already on the calendar for this day.
        #    History events use a different prefix → not fetched → left untouched.
        try:
            existing = fetch_agent_events(target_date, _AGENT_TAG_PREFIX)
        except CalDAVNotConfigured:
            return {**result, "ok": False, "error": "caldav_not_configured"}
        except CalDAVError as exc:
            return {**result, "ok": False, "error": str(exc)}

        # Key everything by the hashed tag key so it matches what fetch parses.
        desired = {_tag_key(block_key(b)): b for b in writable}
        existing_by_key: dict[str, list[dict]] = {}
        for ev in existing:
            existing_by_key.setdefault(ev["key"], []).append(ev)

        # 2. Added / changed / unchanged.
        for tk, b in desired.items():
            evs = existing_by_key.get(tk)
            if evs and len(evs) == 1 and _unchanged(evs[0], b):
                result["unchanged"] += 1
                continue
            if evs:  # changed (or duplicate) — delete old first, transactionally
                try:
                    result["deleted"] += delete_events_with_tag(target_date, _block_tag(b))
                except (CalDAVError, CalDAVNotConfigured) as exc:
                    result["failed"].append(
                        {"block_key": block_key(b), "op": "change-delete", "reason": str(exc)})
                    continue  # do NOT write — avoid duplicate on failed delete
            try:
                write_event(b.title, b.start, b.end, _block_description(b),
                            tag=_active_tag_str(b))
                result["written"] += 1
            except (ValueError, CalDAVError, CalDAVNotConfigured) as exc:
                result["failed"].append(
                    {"block_key": block_key(b), "op": "change-write" if evs else "add",
                     "reason": str(exc)})

        # 3. Removed / stale — active event on calendar but no longer desired.
        for tk, evs in existing_by_key.items():
            if tk in desired:
                continue
            try:
                result["deleted"] += delete_events_with_tag(target_date, _tag_for_key(tk))
            except (CalDAVError, CalDAVNotConfigured) as exc:
                result["failed"].append(
                    {"block_key": tk or "(bare)", "op": "remove", "reason": str(exc)})

        nodes._calendar_cache.pop(target_date, None)
        result["ok"] = not result["failed"]
        return result

    return await asyncio.to_thread(_do_write)


async def write_block_to_calendar(target_date: date, block: TimeBlock) -> dict:
    """
    Write a single block to iCloud Calendar. Idempotent + transactional: deletes
    any previously written event with the same per-block tag first (delete must
    succeed), then writes the new one. Skips non-scheduling block types silently.

    Returns {written, deleted, unchanged, failed:[...], skipped, ok}.
    """
    if block.block_type not in (BlockType.scheduled, BlockType.suggested):
        return {"written": 0, "deleted": 0, "unchanged": 0,
                "failed": [], "skipped": True, "ok": True}

    def _do_write() -> dict:
        from integrations.caldav_client import (
            write_event, delete_events_with_tag,
            CalDAVError, CalDAVNotConfigured,
        )
        result: dict = {"written": 0, "deleted": 0, "unchanged": 0,
                        "failed": [], "skipped": False}
        key = block_key(block)
        try:
            result["deleted"] += delete_events_with_tag(target_date, _block_tag(block))
        except (CalDAVError, CalDAVNotConfigured) as exc:
            result["failed"].append({"block_key": key, "op": "delete", "reason": str(exc)})
            return {**result, "ok": False, "error": str(exc)}
        try:
            write_event(block.title, block.start, block.end, _block_description(block),
                        tag=_active_tag_str(block))
            result["written"] = 1
        except (ValueError, CalDAVError, CalDAVNotConfigured) as exc:
            result["failed"].append({"block_key": key, "op": "write", "reason": str(exc)})
            return {**result, "ok": False, "error": str(exc)}
        nodes._calendar_cache.pop(target_date, None)
        result["ok"] = True
        return result

    return await asyncio.to_thread(_do_write)


async def promote_block_to_history(
    target_date: date, block: TimeBlock, done_iso: str, project_id: str | None = None,
) -> dict:
    """
    Move a completed block from the active tag namespace to the history one:
    delete the active event, then re-write the same time slot tagged as history
    (+ project + done timestamp). History events are immune to the active-block
    cleanup, so they persist across replans as a record of what was done.

    Transactional: if the delete fails, the history event is NOT written (avoid
    duplicates). Returns {promoted: bool, deleted, failed:[...], ok}.
    """
    pid = project_id or _project_id_for_block(block)

    def _do() -> dict:
        from integrations.caldav_client import (
            write_event, delete_events_with_tag, CalDAVError, CalDAVNotConfigured,
        )
        result: dict = {"promoted": False, "deleted": 0, "failed": []}
        key = block_key(block)
        try:
            result["deleted"] += delete_events_with_tag(target_date, _block_tag(block))
        except (CalDAVError, CalDAVNotConfigured) as exc:
            result["failed"].append({"block_key": key, "op": "history-delete", "reason": str(exc)})
            return {**result, "ok": False, "error": str(exc)}
        tag_lines = [_history_tag(block), f"[agent-done:{done_iso}]"]
        if pid:
            tag_lines.append(_project_tag(pid))
        try:
            write_event(block.title, block.start, block.end, _block_description(block),
                        tag="\n".join(tag_lines))
            result["promoted"] = True
        except (ValueError, CalDAVError, CalDAVNotConfigured) as exc:
            result["failed"].append({"block_key": key, "op": "history-write", "reason": str(exc)})
            return {**result, "ok": False, "error": str(exc)}
        nodes._calendar_cache.pop(target_date, None)
        result["ok"] = True
        return result

    return await asyncio.to_thread(_do)


async def delete_project_events(project_id: str) -> dict:
    """
    Delete ALL of a project's calendar events (active + history) across every
    day, matched by the project tag. Used by DELETE /projects/{id}.
    Returns {deleted, ok, error?}.
    """
    def _do() -> dict:
        from integrations.caldav_client import (
            delete_events_by_tags, CalDAVError, CalDAVNotConfigured,
        )
        try:
            deleted = delete_events_by_tags([_project_tag(project_id)])
        except CalDAVNotConfigured:
            return {"deleted": 0, "ok": False, "error": "caldav_not_configured"}
        except CalDAVError as exc:
            return {"deleted": 0, "ok": False, "error": str(exc)}
        nodes._calendar_cache.clear()  # project spans days; invalidate broadly
        return {"deleted": deleted, "ok": True}

    return await asyncio.to_thread(_do)
