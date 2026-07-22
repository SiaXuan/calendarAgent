"""Deterministic plan date-shifting (Phase 4). Pure, no LLM."""
from datetime import date, timedelta

from agents.plan_reschedule import _monday_of, apply_adjustment, is_noop
from models.plan_import import CandidateTask, ImportAdjustment


def _c(title, day=None, week=None, wd=None):
    return CandidateTask(title=title, explicit_date=day, week_index=week, due_weekday=wd)


def test_noop_leaves_dates_untouched():
    adj = ImportAdjustment()
    assert is_noop(adj)
    tasks = [_c("A", date(2025, 9, 8))]
    assert apply_adjustment(tasks, adj) == tasks


def test_term_week_model():
    # New term anchored at a concrete date; week_index drives the offset, weekday snaps.
    adj = ImportAdjustment(term_start_date=date(2027, 9, 6), due_weekday=0)  # Mondays
    week1_monday = _monday_of(date(2027, 9, 6))
    out = apply_adjustment(
        [_c("HW1", week=1), _c("HW2", week=2), _c("HW6", week=6)], adj)
    assert out[0].explicit_date == week1_monday
    assert out[1].explicit_date == week1_monday + timedelta(weeks=1)
    assert out[2].explicit_date == week1_monday + timedelta(weeks=5)
    assert all(t.explicit_date.weekday() == 0 for t in out)


def test_term_week_weekday_override():
    # "class moved to Wednesday" → weekday 2, still week-indexed.
    adj = ImportAdjustment(term_start_date=date(2027, 9, 6), due_weekday=2)
    out = apply_adjustment([_c("HW3", week=3)], adj)
    assert out[0].explicit_date.weekday() == 2
    assert out[0].explicit_date == _monday_of(date(2027, 9, 6)) + timedelta(weeks=2, days=2)


def test_year_shift_preserves_nth_weekday():
    # 2025-09-08 is the 2nd Monday of Sept. Shift to 2027 → 2nd Monday of Sept 2027.
    assert date(2025, 9, 8).weekday() == 0
    adj = ImportAdjustment(target_year=2027)
    out = apply_adjustment([_c("HW1", date(2025, 9, 8))], adj)
    d = out[0].explicit_date
    assert d.year == 2027 and d.month == 9 and d.weekday() == 0
    assert (d.day - 1) // 7 + 1 == 2                # still the 2nd Monday


def test_year_shift_with_weekday_override():
    adj = ImportAdjustment(target_year=2027, due_weekday=2)  # → Wednesdays
    out = apply_adjustment([_c("HW1", date(2025, 9, 8))], adj)
    d = out[0].explicit_date
    assert d.year == 2027 and d.month == 9 and d.weekday() == 2
    assert (d.day - 1) // 7 + 1 == 2                # 2nd Wednesday


def test_shift_weeks():
    adj = ImportAdjustment(shift_weeks=1)
    out = apply_adjustment([_c("A", date(2025, 9, 8))], adj)
    assert out[0].explicit_date == date(2025, 9, 15)


def test_weekday_name_coercion():
    assert CandidateTask(title="x", due_weekday="Monday").due_weekday == 0
    assert CandidateTask(title="x", due_weekday="周三").due_weekday == 2
    assert ImportAdjustment(due_weekday="wednesday").due_weekday == 2
    assert CandidateTask(title="x", due_weekday="nonsense").due_weekday is None
