"""
Tests for agents/calendar_agent.classify_event (Phase 2 + tag-prefix fix).

Regression: agent-synced events carry a per-block tag
"[agent-scheduled:dayflow:KEY]". classify_event must recognize them as
`scheduled` (so fetch_fixed_blocks excludes them) — otherwise a regenerate
after a calendar sync re-imports them as `fixed` and every task duplicates.
"""
from agents.calendar_agent import classify_event
from agents.calendar_writeback import _block_tag
from integrations.caldav_client import AGENT_DESC_TAG
from models.schedule import BlockType, TimeBlock
from datetime import datetime


def _ev(description: str) -> dict:
    return {"description": description, "title": "x"}


def test_user_event_is_fixed():
    assert classify_event(_ev("dentist appointment")) == BlockType.fixed


def test_empty_description_is_fixed():
    assert classify_event(_ev("")) == BlockType.fixed


def test_bare_agent_tag_is_scheduled():
    assert classify_event(_ev(f"notes\n{AGENT_DESC_TAG}")) == BlockType.scheduled


def test_per_block_tag_is_scheduled():
    """The exact bug: per-block tagged events must classify as scheduled, not fixed."""
    blk = TimeBlock(
        start=datetime(2026, 6, 23, 9, 0), end=datetime(2026, 6, 23, 10, 0),
        block_type=BlockType.scheduled, task_id="reminder_leetcode", title="LeetCode",
    )
    desc = f"Cognitive load: deep\n{_block_tag(blk)}"
    assert classify_event(_ev(desc)) == BlockType.scheduled
