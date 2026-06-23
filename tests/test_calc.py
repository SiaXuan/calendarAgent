"""Tests for agents/calc.py — exact date/capacity math, no LLM."""
from datetime import date, datetime

from agents.calc import capacity_check, working_hours_until
from models.solver import FixedInterval


TODAY = date(2026, 6, 15)   # Monday


# ─── working_hours_until ────────────────────────────────────────────────────

def test_same_day_deadline():
    r = working_hours_until(TODAY, today=TODAY, work_start_hour=8, work_end_hour=22)
    assert r["days"] == 1
    assert r["working_minutes"] == 14 * 60
    assert r["is_overdue"] is False


def test_overdue_deadline():
    r = working_hours_until(date(2026, 6, 14), today=TODAY)
    assert r["is_overdue"] is True
    assert r["working_minutes"] == 0


def test_multi_day_includes_weekends_by_default():
    # Mon 6/15 → Wed 6/17 inclusive = 3 days
    r = working_hours_until(date(2026, 6, 17), today=TODAY, work_start_hour=8, work_end_hour=18)
    assert r["days"] == 3
    assert r["working_minutes"] == 3 * 10 * 60


def test_exclude_weekends():
    # Fri 6/19 → Mon 6/22; excluding Sat/Sun → Fri, Mon = 2 working days
    r = working_hours_until(
        date(2026, 6, 22), today=date(2026, 6, 19),
        work_start_hour=8, work_end_hour=18, include_weekends=False,
    )
    assert r["days"] == 2


# ─── capacity_check ─────────────────────────────────────────────────────────

def test_capacity_no_fixed_blocks():
    r = capacity_check(TODAY, [], committed_min=120, work_start_hour=8, work_end_hour=22)
    assert r["free_min"] == 14 * 60
    assert r["deficit_min"] == 0
    assert r["oversubscribed"] is False


def test_capacity_with_fixed_blocks():
    fixed = [FixedInterval(
        start=datetime(2026, 6, 15, 12, 0), end=datetime(2026, 6, 15, 13, 0),
    )]
    r = capacity_check(TODAY, fixed, committed_min=60, work_start_hour=8, work_end_hour=22)
    assert r["free_min"] == 14 * 60 - 60   # one hour blocked


def test_capacity_oversubscribed():
    r = capacity_check(TODAY, [], committed_min=15 * 60, work_start_hour=8, work_end_hour=22)
    # 15h committed, only 14h free → 60min deficit
    assert r["deficit_min"] == 60
    assert r["oversubscribed"] is True


def test_capacity_ignores_other_day_fixed():
    """A fixed block on a different day shouldn't reduce today's capacity."""
    fixed = [FixedInterval(
        start=datetime(2026, 6, 16, 9, 0), end=datetime(2026, 6, 16, 17, 0),
    )]
    r = capacity_check(TODAY, fixed, committed_min=60, work_start_hour=8, work_end_hour=22)
    assert r["free_min"] == 14 * 60
