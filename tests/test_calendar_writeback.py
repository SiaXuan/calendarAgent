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


def test_block_without_task_id_uses_start_based_key():
    blk = TimeBlock(
        start=datetime(2026, 6, 15, 9, 0), end=datetime(2026, 6, 15, 10, 0),
        block_type=BlockType.scheduled, task_id=None, title="X",
    )
    tag = _block_tag(blk)
    assert _AGENT_TAG_PREFIX in tag
    assert "block-" in tag
