"""
Regression test for the calendar write-back tag matching.

Bug: clicking "Sync to Calendar" twice left the previous events behind because
the full-day cleanup matched on the bare tag "[agent-scheduled:dayflow]" which
is NOT a substring of a per-block tag "[agent-scheduled:dayflow:KEY]" (the
trailing "]" breaks it). Fix: match on the bracket-less prefix.
"""
from datetime import datetime

from agents.calendar_writeback import _AGENT_TAG_PREFIX, _block_tag
from integrations.caldav_client import AGENT_DESC_TAG
from models.schedule import BlockType, TimeBlock


def _block(task_id, title="X"):
    return TimeBlock(
        start=datetime(2026, 6, 15, 9, 0), end=datetime(2026, 6, 15, 10, 0),
        block_type=BlockType.scheduled, task_id=task_id, title=title,
    )


def test_prefix_is_substring_of_per_block_tag():
    """The full-day delete prefix must match every per-block tagged event."""
    per_block = _block_tag(_block("reminder_abc"))
    assert _AGENT_TAG_PREFIX in per_block


def test_prefix_is_substring_of_bare_tag():
    assert _AGENT_TAG_PREFIX in AGENT_DESC_TAG


def test_bare_tag_is_NOT_substring_of_per_block_tag():
    """Documents the original bug: this is exactly why the bare tag failed."""
    per_block = _block_tag(_block("reminder_abc"))
    assert AGENT_DESC_TAG not in per_block   # the bug


def test_per_block_tags_distinct_per_task():
    a = _block_tag(_block("task_a"))
    b = _block_tag(_block("task_b"))
    assert a != b
    # both share the prefix that full-day cleanup uses
    assert _AGENT_TAG_PREFIX in a and _AGENT_TAG_PREFIX in b


def test_block_without_task_id_gets_stable_distinct_tag():
    """Blocks without a task_id still get a stable, prefixed per-block tag."""
    blk = TimeBlock(
        start=datetime(2026, 6, 15, 9, 0), end=datetime(2026, 6, 15, 10, 0),
        block_type=BlockType.scheduled, task_id=None, title="X",
    )
    other = TimeBlock(
        start=datetime(2026, 6, 15, 11, 0), end=datetime(2026, 6, 15, 12, 0),
        block_type=BlockType.scheduled, task_id=None, title="X",
    )
    assert _AGENT_TAG_PREFIX in _block_tag(blk)
    assert _block_tag(blk) == _block_tag(blk)          # stable
    assert _block_tag(blk) != _block_tag(other)        # start-distinguished


def test_title_with_bracket_does_not_break_tag():
    """Titles containing ']' must not corrupt the tag (why we hash the key)."""
    blk = TimeBlock(
        start=datetime(2026, 6, 15, 9, 0), end=datetime(2026, 6, 15, 10, 0),
        block_type=BlockType.scheduled, task_id="t1", title="Fix [bug] ]]",
    )
    tag = _block_tag(blk)
    assert _AGENT_TAG_PREFIX in tag
    assert tag.count("]") == 1 and tag.endswith("]")   # exactly one clean terminator


def test_same_parent_different_subtasks_get_distinct_tags():
    """Two subtasks of the same parent must NOT collide (the Step 0/1 key fix)."""
    a = _block_tag(_block("t1", title="Research"))
    b = _block_tag(_block("t1", title="Write"))
    assert a != b
