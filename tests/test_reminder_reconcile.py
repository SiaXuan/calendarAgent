"""
Pure reminder-reconcile diff (Phase 4). No LLM, no I/O — the change-set logic
is a pure function, so these tests build Subtasks/snapshots directly.
"""
from datetime import date

from agents.calendar_writeback import _tag_key
from agents.project_service import content_hash, subtask_block_key
from agents.reminder_reconcile import (
    affected_dates, reconcile_reminders, _reminder_tag,
)
from models.project import PlanSnapshotItem
from models.task import CognitiveLoad, Subtask, TaskKind


def _sub(parent, title, day, minutes=60, load=CognitiveLoad.deep):
    return Subtask(
        parent_id=parent, title=title, cognitive_load=load,
        task_kind=TaskKind.analytical, estimated_minutes=minutes,
        suggested_date=day, project_id="proj",
    )


def _snap(s: Subtask) -> PlanSnapshotItem:
    """Snapshot item matching a subtask's current content (→ 'unchanged')."""
    bkey = subtask_block_key(s)
    return PlanSnapshotItem(
        block_key=bkey, task_id=s.parent_id, title=s.title,
        suggested_date=s.suggested_date, content_hash=content_hash(s),
    )


def _current(s: Subtask) -> dict:
    """A reminder the frontend already owns for this subtask."""
    return {"tag_key": _tag_key(subtask_block_key(s)),
            "title": s.title, "due": str(s.suggested_date)}


def test_first_replan_all_create():
    subs = [_sub("t1", "Read paper", date(2026, 8, 1)),
            _sub("t1", "Draft", date(2026, 8, 8))]
    cs = reconcile_reminders(subs, current_reminders=[], old_snapshot=[])
    assert {c["title"] for c in cs["create"]} == {"Read paper", "Draft"}
    assert not cs["update"] and not cs["delete"] and cs["unchanged"] == 0
    # spec carries stable identity + due
    spec = cs["create"][0]
    assert spec["tag"] == _reminder_tag(spec["block_key"])
    assert spec["due"].startswith("2026-08")


def test_changed_vs_unchanged():
    keep = _sub("t1", "Keep", date(2026, 8, 1))
    changed_old = _sub("t1", "Move", date(2026, 8, 8))
    old_snapshot = [_snap(keep), _snap(changed_old)]
    current = [_current(keep), _current(changed_old)]

    # "Move" now falls on a different day → content_hash differs → update.
    changed_new = _sub("t1", "Move", date(2026, 8, 1))
    cs = reconcile_reminders([keep, changed_new], current, old_snapshot)
    assert [u["title"] for u in cs["update"]] == ["Move"]
    assert cs["unchanged"] == 1
    assert not cs["create"] and not cs["delete"]


def test_dropped_node_is_deleted():
    a = _sub("t1", "A", date(2026, 8, 1))
    b = _sub("t1", "B", date(2026, 8, 8))
    old_snapshot = [_snap(a), _snap(b)]
    current = [_current(a), _current(b)]
    # new plan no longer has B
    cs = reconcile_reminders([a], current, old_snapshot)
    assert [d["tag_key"] for d in cs["delete"]] == [_tag_key("t1::B")]
    assert cs["unchanged"] == 1 and not cs["create"] and not cs["update"]


def test_done_node_left_untouched():
    a = _sub("t1", "A", date(2026, 8, 1))
    done = _sub("t1", "Done", date(2026, 8, 8))
    old_snapshot = [_snap(a), _snap(done)]
    current = [_current(a), _current(done)]
    done_keys = {subtask_block_key(done)}

    cs = reconcile_reminders([a, done], current, old_snapshot, done_keys=done_keys)
    # the done node appears in NO list (not deleted, not updated, not created)
    for bucket in ("create", "update"):
        assert all(s["block_key"] != "t1::Done" for s in cs[bucket])
    assert all(d["tag_key"] != _tag_key("t1::Done") for d in cs["delete"])
    assert cs["unchanged"] == 1  # only "A"


def test_title_with_brackets_matches_cleanly():
    # A ']'/':' in the title must not break tag matching (hashing isolates it).
    weird = _sub("t1", "Read [ch. 3]: notes", date(2026, 8, 1))
    cs1 = reconcile_reminders([weird], current_reminders=[], old_snapshot=[])
    assert len(cs1["create"]) == 1
    # once present, a re-run with matching snapshot → unchanged, not a duplicate
    cs2 = reconcile_reminders([weird], [_current(weird)], [_snap(weird)])
    assert cs2["unchanged"] == 1 and not cs2["create"] and not cs2["delete"]


def test_affected_dates():
    a = _sub("t1", "A", date(2026, 8, 1))
    b = _sub("t1", "B", date(2026, 8, 8))
    old_snapshot = [_snap(a), _snap(b)]
    cs = reconcile_reminders([a], [_current(a), _current(b)], old_snapshot)
    # A unchanged (no due surfaced), B deleted → its old day surfaces
    dates = affected_dates(cs, old_snapshot)
    assert "2026-08-08" in dates
