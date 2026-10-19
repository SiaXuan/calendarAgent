"""
Calendar block identity: the tags and keys that say "this event is ours".

Pure functions only — no I/O. The change-set reconcilers (calendar_reconcile,
reminder_reconcile) and the project layer build on these; the Swift client
derives the same tags so both sides agree on which event belongs to which block.

The CalDAV write functions that used to live here moved to legacy/caldav/
(2026-10-19) when the client took over all calendar I/O via EventKit.
"""
import hashlib
from datetime import date, datetime

from models.schedule import BlockType, TimeBlock
from storage import completion_store, task_store
from models.project import CompletionStatus

# The bare day-level tag. Defined here rather than imported from the CalDAV
# client: it describes how a block is marked, not how it is transported.
AGENT_DESC_TAG = "[agent-scheduled:dayflow]"

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
