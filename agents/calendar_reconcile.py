"""
Pure calendar reconcile (Phase 4 — local/EventKit architecture).

The backend NEVER touches the OS calendar (see docs/ARCHITECTURE.md §0). Instead
the Swift/EventKit frontend uploads the current agent-owned events, and these
pure functions diff them against the desired schedule and return a **change-set**
(create / update / delete) that the frontend applies via EventKit.

This preserves the Step 0 reconcile policy — leave unchanged blocks alone, always
delete-before-write for a changed key (so a failed delete can't duplicate),
skip completed blocks (they live as history) — but as a computed change-set
rather than direct CalDAV calls. Tag helpers are reused from calendar_writeback
(they are pure; only its network calls are being retired).
"""
from datetime import date, datetime

from agents.calendar_writeback import (
    _AGENT_TAG_PREFIX, _active_tag_str, _block_description, _block_tag,
    _project_id_for_block, _tag_for_key, _tag_key, block_key,
)
from models.schedule import BlockType, TimeBlock


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, (datetime, date)) else str(v)


def _event_spec(block: TimeBlock) -> dict:
    """Everything the frontend needs to create/update one calendar event."""
    desc = _block_description(block)
    tag = _active_tag_str(block)
    notes = f"{desc}\n{tag}" if desc else tag
    return {
        "block_key": block_key(block),
        "tag_key": _tag_key(block_key(block)),
        "tag": _block_tag(block),          # the identity tag line (for matching)
        "title": block.title,
        "start": block.start.isoformat(),
        "end": block.end.isoformat(),
        "description": desc,
        "notes": notes,                    # full notes to write (desc + tags)
        "project_id": _project_id_for_block(block),
    }


def _unchanged(existing: dict, block: TimeBlock) -> bool:
    """Existing event already matches desired (title + start + end) → leave it.
    Duplicate-safe either way: a false 'changed' only re-writes, never duplicates,
    because a changed key is always delete-before-create by the frontend."""
    return (
        existing.get("title") == block.title
        and _iso(existing.get("start")) == block.start.isoformat()
        and _iso(existing.get("end")) == block.end.isoformat()
    )


def reconcile_schedule(
    desired_blocks: list[TimeBlock],
    current_events: list[dict],
    done_keys: set[str] | None = None,
) -> dict:
    """
    Diff desired scheduled/suggested blocks against the frontend-supplied current
    agent events and return a change-set.

    current_events: [{tag_key, title, start, end}] — agent-owned events currently
      on the calendar for this day (frontend reads these via EventKit; identified
      by the per-block tag in the event notes).
    done_keys: block_keys marked complete — skipped here (they live as history).

    Returns {create:[EventSpec], update:[EventSpec], delete:[{tag_key, tag}],
             unchanged:int}. The frontend applies delete-before-create per key.
    """
    done_keys = done_keys or set()
    active = [
        b for b in desired_blocks
        if b.block_type in (BlockType.scheduled, BlockType.suggested)
        and block_key(b) not in done_keys
    ]
    desired_by_tk = {_tag_key(block_key(b)): b for b in active}

    current_by_tk: dict[str, list[dict]] = {}
    for ev in current_events:
        current_by_tk.setdefault(ev.get("tag_key", ""), []).append(ev)

    create, update, delete = [], [], []
    unchanged = 0
    for tk, b in desired_by_tk.items():
        evs = current_by_tk.get(tk)
        if evs and len(evs) == 1 and _unchanged(evs[0], b):
            unchanged += 1
        elif evs:
            update.append(_event_spec(b))     # frontend: replace matching event(s)
        else:
            create.append(_event_spec(b))

    for tk, evs in current_by_tk.items():
        if tk not in desired_by_tk:
            delete.append({"tag_key": tk, "tag": _tag_for_key(tk)})

    return {"create": create, "update": update, "delete": delete, "unchanged": unchanged}
