"""
Pure change-set reconcile (local/EventKit architecture).

No network, no CalDAV — the backend computes create/update/delete from the
frontend-supplied current events. Same policy as the old block-diff: unchanged
left alone, changed → update, removed → delete, completed → skipped.
"""
from datetime import date, datetime

from agents.calendar_reconcile import reconcile_schedule
from agents.calendar_writeback import _tag_key, block_key
from models.schedule import BlockType, TimeBlock


def _blk(task_id, title, hour):
    return TimeBlock(
        start=datetime(2026, 6, 15, hour, 0), end=datetime(2026, 6, 15, hour + 1, 0),
        block_type=BlockType.scheduled, task_id=task_id, title=title,
    )


def _cur(task_id, title, hour, event_title=None):
    return {
        "tag_key": _tag_key(f"{task_id}::{title}"),
        "title": event_title if event_title is not None else title,
        "start": datetime(2026, 6, 15, hour, 0).isoformat(),
        "end": datetime(2026, 6, 15, hour + 1, 0).isoformat(),
    }


def test_reconcile_classifies_create_update_delete_unchanged():
    desired = [_blk("a", "A", 9), _blk("b", "B", 10), _blk("c", "C", 11)]
    current = [
        _cur("a", "A", 9),                       # matches → unchanged
        _cur("b", "B", 10, event_title="B-old"),  # differs → update
        _cur("d", "D", 14),                       # not desired → delete
    ]
    cs = reconcile_schedule(desired, current)

    assert cs["unchanged"] == 1
    assert [s["title"] for s in cs["create"]] == ["C"]
    assert [s["title"] for s in cs["update"]] == ["B"]
    assert cs["delete"][0]["tag_key"] == _tag_key("d::D")


def test_completed_block_is_skipped_not_recreated():
    """A done block must not be emitted as create (it lives as history)."""
    desired = [_blk("a", "A", 9)]
    cs = reconcile_schedule(desired, current_events=[], done_keys={block_key(_blk("a", "A", 9))})
    assert cs["create"] == [] and cs["update"] == [] and cs["unchanged"] == 0


def test_event_spec_carries_tag_and_notes():
    desired = [_blk("t1", "Write", 9)]
    cs = reconcile_schedule(desired, current_events=[])
    spec = cs["create"][0]
    assert spec["block_key"] == "t1::Write"
    assert "[agent-scheduled:dayflow:" in spec["tag"]
    assert spec["tag"] in spec["notes"]          # tag embedded in the notes to write
    assert spec["start"] == datetime(2026, 6, 15, 9, 0).isoformat()
