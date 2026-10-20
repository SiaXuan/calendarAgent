"""Tests for ScheduleScratch + bound tools (Phase: 核心闭环 S2)."""
from datetime import date, datetime

import pytest

from agents.scratch import ScheduleScratch, classify_impact
from agents.tools.schedule_tools import make_schedule_tools
from models.schedule import BlockType, TimeBlock
from models.task import CognitiveLoad, TaskKind


D = date(2026, 6, 15)


def _scheduled(task_id, title, h0, h1):
    return TimeBlock(
        start=datetime(2026, 6, 15, h0, 0), end=datetime(2026, 6, 15, h1, 0),
        block_type=BlockType.scheduled, task_id=task_id, title=title,
        cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
    )


def _fixed(title, h0, h1):
    return TimeBlock(
        start=datetime(2026, 6, 15, h0, 0), end=datetime(2026, 6, 15, h1, 0),
        block_type=BlockType.fixed, title=title,
    )


def _scratch(blocks, **kw):
    return ScheduleScratch(D, blocks, base_version=1,
                           work_start_hour=8, work_end_hour=22, **kw)


# ─── ScheduleScratch core ───────────────────────────────────────────────────

def test_render_lists_blocks_with_ids():
    s = _scratch([_scheduled("t1", "Work", 9, 10), _fixed("Meet", 13, 14)])
    r = s.render()
    assert "[b1]" in r and "[b2]" in r
    assert "Work" in r and "Meet" in r


def test_move_block_updates_and_preserves_duration():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.move_block("b1", datetime(2026, 6, 15, 11, 0))
    b = s.get("b1")
    assert b.start == datetime(2026, 6, 15, 11, 0)
    assert b.end == datetime(2026, 6, 15, 12, 0)   # 1h preserved


def test_cannot_move_fixed_block():
    s = _scratch([_fixed("Meet", 13, 14)])
    with pytest.raises(ValueError):
        s.move_block("b1", datetime(2026, 6, 15, 15, 0))


def test_remove_scheduled_block():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.remove_block("b1")
    assert s.get("b1") is None


def test_cannot_remove_fixed():
    s = _scratch([_fixed("Meet", 13, 14)])
    with pytest.raises(ValueError):
        s.remove_block("b1")


def test_add_fixed_event():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.add_fixed_event("Dentist", datetime(2026, 6, 15, 15, 0), datetime(2026, 6, 15, 16, 0))
    titles = [b.title for b in s.committed_blocks()]
    assert "Dentist" in titles


def test_resize_block_changes_duration_only():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.resize_block("b1", 90)
    b = s.get("b1")
    assert b.start == datetime(2026, 6, 15, 9, 0)      # start unchanged
    assert b.end == datetime(2026, 6, 15, 10, 30)      # 90min


def test_resize_rejects_nonpositive_and_fixed():
    s = _scratch([_scheduled("t1", "Work", 9, 10), _fixed("Meet", 13, 14)])
    with pytest.raises(ValueError):
        s.resize_block("b1", 0)
    with pytest.raises(ValueError):
        s.resize_block("b2", 60)   # fixed block


def test_add_to_schedule_carries_notes():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    tools = {t.name: t for t in make_schedule_tools(s)}
    tools["add_to_schedule"].invoke({
        "title": "Interview @ Acme", "start_time": "15:00",
        "end_time": "16:00", "notes": "from email 2026-06-14",
    })
    added = [b for b in s.committed_blocks() if b.title == "Interview @ Acme"][0]
    assert added.block_type == BlockType.fixed
    assert added.notes == "from email 2026-06-14"


# ─── diff + classify_impact (the deterministic gate) ────────────────────────

def test_single_move_is_minor():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.move_block("b1", datetime(2026, 6, 15, 11, 0))
    assert classify_impact(s.diff()) == "minor"


def test_move_records_from_and_to_times():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.move_block("b1", datetime(2026, 6, 15, 14, 0))
    change = s.diff().moved[0]
    assert change.from_time == "09:00"
    assert change.to_time == "14:00"


def test_cross_day_move_times_include_date():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.move_block("b1", datetime(2026, 6, 16, 9, 0))
    change = s.diff().moved[0]
    assert "6/15" in change.from_time and "09:00" in change.from_time
    assert "6/16" in change.to_time


def test_remove_records_from_time_only():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.remove_block("b1")
    change = s.diff().removed[0]
    assert change.from_time == "09:00"
    assert change.to_time is None


def test_two_moves_is_major():
    s = _scratch([_scheduled("t1", "A", 9, 10), _scheduled("t2", "B", 11, 12)])
    s.move_block("b1", datetime(2026, 6, 15, 14, 0))
    s.move_block("b2", datetime(2026, 6, 15, 16, 0))
    assert classify_impact(s.diff()) == "major"


def test_any_delete_is_major():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.remove_block("b1")
    assert classify_impact(s.diff()) == "major"


def test_add_fixed_is_major():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.add_fixed_event("Dentist", datetime(2026, 6, 15, 15, 0), datetime(2026, 6, 15, 16, 0))
    assert classify_impact(s.diff()) == "major"


def test_cross_day_move_is_major():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    s.move_block("b1", datetime(2026, 6, 16, 9, 0))  # next day
    d = s.diff()
    assert d.moved[0].cross_day is True
    assert classify_impact(d) == "major"


def test_touching_synced_block_is_major():
    blk = _scheduled("t1", "Work", 9, 10)
    s = _scratch([blk], synced_keys={"t1::Work"})
    s.move_block("b1", datetime(2026, 6, 15, 11, 0))
    assert classify_impact(s.diff()) == "major"


def test_empty_diff_is_minor():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    assert s.diff().is_empty
    assert classify_impact(s.diff()) == "minor"


# ─── bound tools ────────────────────────────────────────────────────────────

def test_tools_constructed():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    tools = make_schedule_tools(s)
    names = {t.name for t in tools}
    assert {"get_schedule", "move_block", "remove_block", "add_fixed_event",
            "capacity_check", "working_hours_until"} <= names


def test_move_tool_mutates_scratch():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    tools = {t.name: t for t in make_schedule_tools(s)}
    out = tools["move_block"].invoke({"block_id": "b1", "new_start_time": "14:00"})
    assert "14:00" in out
    assert s.get("b1").start == datetime(2026, 6, 15, 14, 0)


def test_move_tool_returns_error_string_on_bad_id():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    tools = {t.name: t for t in make_schedule_tools(s)}
    out = tools["move_block"].invoke({"block_id": "b99", "new_start_time": "14:00"})
    assert "错误" in out


def test_capacity_check_tool_reports_oversubscription():
    # work hours 8-9 (60min) but 2h of scheduled work
    s = ScheduleScratch(
        D, [_scheduled("t1", "A", 8, 9), _scheduled("t2", "B", 9, 10)],
        base_version=1, work_start_hour=8, work_end_hour=9,
    )
    tools = {t.name: t for t in make_schedule_tools(s)}
    out = tools["capacity_check"].invoke({})
    assert "超额=True" in out


# ─── the date is the scratch's, not the model's ─────────────────────────────

def _one_block_tools():
    from datetime import date, datetime
    from agents.scratch import ScheduleScratch
    from agents.tools.schedule_tools import make_schedule_tools
    from models.schedule import BlockType, TimeBlock
    d = date(2026, 6, 15)
    b = TimeBlock(start=datetime(2026, 6, 15, 9), end=datetime(2026, 6, 15, 10),
                  block_type=BlockType.scheduled, task_id="t1", title="Work")
    sc = ScheduleScratch(d, [b], base_version=1)
    return sc, {t.name: t for t in make_schedule_tools(sc)}


def test_time_of_day_lands_on_the_target_date():
    from datetime import datetime
    sc, tools = _one_block_tools()
    tools["move_block"].invoke({"block_id": "b1", "new_start_time": "14:30"})
    assert sc.committed_blocks()[0].start == datetime(2026, 6, 15, 14, 30)


def test_a_date_other_than_the_target_day_is_refused():
    """
    Scratch doesn't re-check dates, so a wrong one used to land a block dated
    another day inside today's schedule — rendering as "14:00" while stored as
    a different date. The tools take a time of day; a full datetime is only
    accepted when it names the target day.
    """
    from datetime import datetime
    sc, tools = _one_block_tools()
    out = tools["move_block"].invoke({"block_id": "b1", "new_start_time": "2025-06-15T14:00:00"})
    assert "只能安排" in out
    assert sc.committed_blocks()[0].start == datetime(2026, 6, 15, 9)   # untouched


def test_full_datetime_on_the_target_day_is_tolerated():
    from datetime import datetime
    sc, tools = _one_block_tools()
    tools["move_block"].invoke({"block_id": "b1", "new_start_time": "2026-06-15T16:00:00"})
    assert sc.committed_blocks()[0].start == datetime(2026, 6, 15, 16)


def test_event_can_end_at_midnight():
    sc, tools = _one_block_tools()
    out = tools["add_fixed_event"].invoke(
        {"title": "Late shift", "start_time": "22:00", "end_time": "24:00"})
    assert "错误" not in out


def test_external_event_on_another_day_cannot_be_added():
    """add_to_schedule is for today's events; another day's interview has to be
    reported to the user, not quietly placed."""
    sc, tools = _one_block_tools()
    out = tools["add_to_schedule"].invoke({
        "title": "Interview", "start_time": "2026-06-16T10:00:00",
        "end_time": "2026-06-16T11:00:00"})
    assert "只能安排" in out
