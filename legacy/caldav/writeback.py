"""
CalDAV calendar write-back — RETIRED 2026-10-19, kept for reference.

These wrote the user's iCloud calendar from the backend. The Swift client now
owns all calendar I/O through EventKit: the backend returns a change-set
(agents/calendar_reconcile.py) and the client applies it.

They were retired for two reasons beyond the architecture. They had become
unreachable — events live in a local EventKit calendar that CalDAV cannot see,
so a delete here matched nothing. And they were slow in a way users felt:
measured at ~20s per call against a real account even when the tag matched
nothing, because each one pulls the whole calendar down to search it. That
latency showed up as "delete failed, then succeeded on refresh", and as a
20-second wait behind every completion tick.

Nothing imports this module. It is here so the CalDAV protocol work isn't lost
if a future web or iOS client, which can't reach EventKit, needs it again.
"""
import asyncio
import hashlib
from datetime import date, datetime

from agents import nodes  # for _calendar_cache invalidation
from agents.calendar_writeback import (
    AGENT_DESC_TAG, _AGENT_HISTORY_PREFIX, _AGENT_TAG_PREFIX, _active_tag_str,
    _block_description, _block_tag, _history_tag, _is_done, _project_id_for_block,
    _project_tag, _tag_for_key, _tag_key, _unchanged, block_key,
)
from models.schedule import BlockType, TimeBlock
from storage import completion_store, schedule_store, task_store
from models.project import CompletionStatus


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


async def delete_block_from_calendar(target_date: date, block: TimeBlock) -> dict:
    """
    Best-effort delete of a block's active calendar event by its stable per-block
    tag. Called when a synced block is removed from the schedule so the calendar
    doesn't keep an orphaned event. Graceful no-op in local-only mode (no CalDAV).
    Returns {deleted, ok, error?}.
    """
    def _do() -> dict:
        from integrations.caldav_client import (
            delete_events_with_tag, CalDAVError, CalDAVNotConfigured,
        )
        try:
            deleted = delete_events_with_tag(target_date, _block_tag(block))
        except CalDAVNotConfigured:
            return {"deleted": 0, "ok": True}   # nothing written in local-only mode
        except CalDAVError as exc:
            return {"deleted": 0, "ok": False, "error": str(exc)}
        nodes._calendar_cache.pop(target_date, None)
        return {"deleted": deleted, "ok": True}

    return await asyncio.to_thread(_do)


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
