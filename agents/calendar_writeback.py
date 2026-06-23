"""
CalDAV write-back helpers.

Lifted out of agents/orchestrator.py during Phase A migration so the
orchestrator can be deleted once the graph-based path is fully wired.
These functions are not LangGraph nodes — they're triggered by explicit
"sync to calendar" buttons in the UI, not by the schedule generation flow.
"""
import asyncio
from datetime import date

from agents import nodes  # for _calendar_cache invalidation
from integrations.caldav_client import AGENT_DESC_TAG
from models.schedule import BlockType, TimeBlock
from storage import schedule_store

# Bracket-less prefix shared by the bare tag and every per-block tag.
# IMPORTANT: the bare AGENT_DESC_TAG "[agent-scheduled:dayflow]" is NOT a
# substring of a per-block tag "[agent-scheduled:dayflow:KEY]" (the trailing
# "]" breaks it), so full-day cleanup must match on this PREFIX instead —
# otherwise re-syncing leaves the previous events behind (duplicates).
_AGENT_TAG_PREFIX = AGENT_DESC_TAG[:-1]  # "[agent-scheduled:dayflow"


def _block_key(block: TimeBlock) -> str:
    """Stable identifier for a block within a day, used in the per-block CalDAV tag."""
    return block.task_id or f"block-{block.start.isoformat()}"


def _block_tag(block: TimeBlock) -> str:
    """Per-block agent tag: "[agent-scheduled:dayflow:<key>]"."""
    return f"{_AGENT_TAG_PREFIX}:{_block_key(block)}]"


def _block_description(block: TimeBlock) -> str:
    parts: list[str] = []
    if block.cognitive_load:
        parts.append(f"Cognitive load: {block.cognitive_load.value}")
    if block.phase_label:
        parts.append(block.phase_label)
    if block.notes:
        parts.append(block.notes)
    return "\n".join(parts)


async def write_schedule_to_calendar(target_date: date) -> dict:
    """
    Write current schedule_store[target_date] back to iCloud Calendar.
    Clears previously-written agent blocks for that date first so re-runs don't
    duplicate. Only `scheduled` and `suggested` blocks are written.

    Returns {written: int, deleted: int}.
    """
    schedule = schedule_store.get(target_date)
    if schedule is None:
        return {"written": 0, "deleted": 0}

    writable = [
        b for b in schedule.blocks
        if b.block_type in (BlockType.scheduled, BlockType.suggested)
    ]

    def _do_write() -> dict:
        from integrations.caldav_client import write_event, delete_events_with_tag
        # Match on the prefix so BOTH bare and per-block tagged events are wiped.
        deleted = delete_events_with_tag(target_date, _AGENT_TAG_PREFIX)
        written = 0
        for b in writable:
            uid = write_event(b.title, b.start, b.end, _block_description(b), tag=_block_tag(b))
            if uid:
                written += 1
        nodes._calendar_cache.pop(target_date, None)
        return {"written": written, "deleted": deleted}

    return await asyncio.to_thread(_do_write)


async def write_block_to_calendar(target_date: date, block: TimeBlock) -> dict:
    """
    Write a single block to iCloud Calendar. Idempotent: deletes any previously
    written event with the same per-block tag before writing the new one.
    Skips non-scheduling block types (fixed, meal, instant) silently.

    Returns {written: int, deleted: int, skipped: bool}.
    """
    if block.block_type not in (BlockType.scheduled, BlockType.suggested):
        return {"written": 0, "deleted": 0, "skipped": True}

    def _do_write() -> dict:
        from integrations.caldav_client import write_event, delete_events_with_tag
        tag = _block_tag(block)
        deleted = delete_events_with_tag(target_date, tag)
        uid = write_event(block.title, block.start, block.end, _block_description(block), tag=tag)
        nodes._calendar_cache.pop(target_date, None)
        return {"written": 1 if uid else 0, "deleted": deleted, "skipped": False}

    return await asyncio.to_thread(_do_write)
