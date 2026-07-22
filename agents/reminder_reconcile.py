"""
Pure reminder reconcile (Phase 4 — local/EventKit architecture).

Sibling of agents/calendar_reconcile.py. Where that one diffs a single day's
time blocks (calendar events), this one diffs a whole project's FUTURE nodes,
which live as **reminders** (待办) with a due date — not time blocks. On a
node's due day the existing reminder→task→schedule pipeline turns it into that
day's time block; until then it is just a reminder.

The backend NEVER touches the OS reminders (see docs/ARCHITECTURE.md §0). The
Swift/EventKit frontend uploads the reminders it currently owns for a project,
and this pure function diffs them against the freshly decomposed plan, returning
a change-set (create / update / delete) the frontend applies via EventKit.

Completion-aware, mirroring the calendar reconcile policy:
  - done nodes are left untouched (never re-created, updated, or deleted),
  - a node whose content changed → update (frontend deletes-before-creates),
  - an unchanged node → left in place,
  - a node dropped from the new plan → delete.

Identity is the same stable block_key ("{parent_id}::{title}") and short
tag_key hash used everywhere else, so completion / reconcile all key alike.
"""
from datetime import date, datetime

from agents.calendar_writeback import _project_tag, _tag_key
from agents.project_service import content_hash, subtask_block_key
from models.project import PlanSnapshotItem
from models.task import Subtask

# Reminders live in their own tag namespace so day-block cleanup never touches
# them (and vice versa). No history namespace: a done reminder is just checked
# off; completion_store is the source of truth, not the reminder's state.
_REMINDER_TAG_PREFIX = "[agent-reminder:dayflow"


def _reminder_tag_for_key(tag_key: str) -> str:
    """Per-node reminder tag from the short hash: "[agent-reminder:dayflow:<hash>]"."""
    return f"{_REMINDER_TAG_PREFIX}:{tag_key}]"


def _reminder_tag(bkey: str) -> str:
    return _reminder_tag_for_key(_tag_key(bkey))


def _reminder_spec(s: Subtask) -> dict:
    """Everything the frontend needs to create/update one reminder."""
    bkey = subtask_block_key(s)
    due = s.due_datetime or s.suggested_date
    notes_lines: list[str] = []
    if s.phase_label:
        notes_lines.append(s.phase_label)
    notes_lines.append(_reminder_tag(bkey))       # identity tag (for matching)
    if s.project_id:
        notes_lines.append(_project_tag(s.project_id))
    return {
        "block_key": bkey,
        "tag_key": _tag_key(bkey),
        "tag": _reminder_tag(bkey),
        "title": s.title,
        "due": due.isoformat() if due is not None else None,
        "notes": "\n".join(notes_lines),
        "project_id": s.project_id,
    }


def reconcile_reminders(
    desired_subs: list[Subtask],
    current_reminders: list[dict],
    old_snapshot: list[PlanSnapshotItem] | None = None,
    done_keys: set[str] | None = None,
) -> dict:
    """
    Diff the freshly decomposed plan against the frontend-supplied current
    reminders and return a change-set.

    current_reminders: [{tag_key, title, due}] — reminders the frontend currently
      owns for this project (read via EventKit; identified by the per-node tag in
      the reminder notes). Existence (create vs delete) is decided against these.
    old_snapshot: the project's last-written plan; its content_hash decides
      changed-vs-unchanged for a node that already exists.
    done_keys: block_keys marked complete — skipped here (left untouched).

    Returns {create:[ReminderSpec], update:[ReminderSpec],
             delete:[{tag_key, tag}], unchanged:int}. The frontend applies
    delete-before-create per key.
    """
    done_keys = done_keys or set()
    done_tks = {_tag_key(k) for k in done_keys}
    old_by_key = {i.block_key: i for i in (old_snapshot or [])}
    current_by_tk = {r.get("tag_key", ""): r for r in current_reminders}

    create, update, delete = [], [], []
    unchanged = 0
    desired_tks: set[str] = set()
    for s in desired_subs:
        bkey = subtask_block_key(s)
        if bkey in done_keys:
            continue                       # done: leave the reminder as-is
        tk = _tag_key(bkey)
        desired_tks.add(tk)
        old = old_by_key.get(bkey)
        if tk not in current_by_tk:
            create.append(_reminder_spec(s))
        elif old is None or old.content_hash != content_hash(s):
            update.append(_reminder_spec(s))   # frontend: replace matching reminder
        else:
            unchanged += 1

    for tk in current_by_tk:
        if tk and tk not in desired_tks and tk not in done_tks:
            delete.append({"tag_key": tk, "tag": _reminder_tag_for_key(tk)})

    return {"create": create, "update": update, "delete": delete, "unchanged": unchanged}


def affected_dates(
    changeset: dict, old_snapshot: list[PlanSnapshotItem] | None = None,
) -> list[str]:
    """
    Which days a reminder change-set touches, as YYYY-MM-DD strings. The frontend
    uses this to know which days' schedules to refresh via the daily path — most
    notably today, when a node is moved onto it (today's plan then re-flows on the
    normal reminder→task→schedule route; replan itself never writes the calendar).
    """
    dates: set[str] = set()
    for spec in changeset.get("create", []) + changeset.get("update", []):
        if spec.get("due"):
            dates.add(spec["due"][:10])
    old_by_tk = {_tag_key(i.block_key): i for i in (old_snapshot or [])}
    for d in changeset.get("delete", []):
        item = old_by_tk.get(d.get("tag_key"))
        if item and item.suggested_date:
            dates.add(str(item.suggested_date))
    return sorted(dates)
