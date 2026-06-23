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
    out = tools["move_block"].invoke({"block_id": "b1", "new_start_iso": "2026-06-15T14:00:00"})
    assert "14:00" in out
    assert s.get("b1").start == datetime(2026, 6, 15, 14, 0)


def test_move_tool_returns_error_string_on_bad_id():
    s = _scratch([_scheduled("t1", "Work", 9, 10)])
    tools = {t.name: t for t in make_schedule_tools(s)}
    out = tools["move_block"].invoke({"block_id": "b99", "new_start_iso": "2026-06-15T14:00:00"})
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
