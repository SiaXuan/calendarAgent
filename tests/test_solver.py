"""Tests for agents/solver.py — pure multi-day solver, no LLM."""
from datetime import date, datetime, timedelta

from agents.solver import solve_schedule
from models.solver import (
    FixedInterval, SolveStatus, SolverConstraints, SolverTask,
)
from models.task import CognitiveLoad, TaskKind


D0 = date(2026, 6, 15)   # Monday
D1 = date(2026, 6, 16)
D2 = date(2026, 6, 17)

# High energy mornings, low afternoons — classic curve.
HIGH_MORNING = [0.1] * 24
for _h in range(8, 12):
    HIGH_MORNING[_h] = 0.9
for _h in range(12, 22):
    HIGH_MORNING[_h] = 0.4

FLAT = [0.5] * 24
LOW = [0.3] * 24


def _task(id, mins, load=CognitiveLoad.deep, deadline=None, must_do=False, kind=TaskKind.analytical):
    return SolverTask(
        id=id, title=id, duration_min=mins, cognitive_load=load,
        task_kind=kind, deadline=deadline, must_do=must_do,
    )


def _curves(*days_curves):
    return dict(days_curves)


# ─── basic feasible placement ───────────────────────────────────────────────

def test_single_task_placed():
    r = solve_schedule(
        [_task("t1", 60)], [], [D0], _curves((D0, FLAT)),
    )
    assert r.status == SolveStatus.feasible
    assert len(r.placements) == 1
    assert r.placements[0].task_id == "t1"
    assert r.placements[0].day == D0


def test_deep_task_prefers_high_energy_window():
    """A deep task should land in the high-energy morning, not the low afternoon."""
    r = solve_schedule(
        [_task("deep", 60, load=CognitiveLoad.deep)],
        [], [D0], _curves((D0, HIGH_MORNING)),
        SolverConstraints(work_start_hour=8, work_end_hour=22),
    )
    assert r.status == SolveStatus.feasible
    # Morning window is 8-12 (energy 0.9). Placement should start before noon.
    assert r.placements[0].start.hour < 12


def test_low_energy_day_inflates_duration():
    """On a low-energy day with inflate_factor, effective duration grows."""
    r = solve_schedule(
        [_task("t1", 60, load=CognitiveLoad.deep)],
        [], [D0], _curves((D0, LOW)),
        SolverConstraints(inflate_factor=1.5, low_energy_threshold=0.45),
    )
    p = r.placements[0]
    dur = int((p.end - p.start).total_seconds() // 60)
    assert dur == 90   # 60 * 1.5
    assert p.inflated is True


def test_no_inflation_on_high_energy_day():
    r = solve_schedule(
        [_task("t1", 60)], [], [D0], _curves((D0, FLAT)),
        SolverConstraints(inflate_factor=1.5, low_energy_threshold=0.45),
    )
    p = r.placements[0]
    assert int((p.end - p.start).total_seconds() // 60) == 60
    assert p.inflated is False


# ─── multi-day: defer when today is full ────────────────────────────────────

def test_non_urgent_task_defers_to_later_day_when_today_full():
    """
    Today is packed with an urgent must-do; a non-urgent task should land on a
    later day rather than fail.
    """
    # Urgent task fills most of day 0; non-urgent has a far deadline.
    urgent = _task("urgent", 60, deadline=D0, must_do=True)
    relaxed = _task("relaxed", 60, deadline=D2)
    # Day 0 work window tiny (8-9 = 60 min) so only one task fits.
    r = solve_schedule(
        [urgent, relaxed], [], [D0, D1, D2],
        _curves((D0, FLAT), (D1, FLAT), (D2, FLAT)),
        SolverConstraints(work_start_hour=8, work_end_hour=9),
    )
    assert r.status == SolveStatus.feasible
    placed = {p.task_id: p.day for p in r.placements}
    assert placed["urgent"] == D0
    # relaxed got pushed to a later day
    assert placed["relaxed"] in (D1, D2)


def test_urgent_task_lands_on_earliest_day():
    urgent = _task("u", 60, deadline=D0, must_do=True)
    r = solve_schedule(
        [urgent], [], [D0, D1, D2],
        _curves((D0, FLAT), (D1, FLAT), (D2, FLAT)),
    )
    assert r.placements[0].day == D0


# ─── infeasible: oversubscribed → bottleneck + relaxations ──────────────────

def test_oversubscribed_returns_infeasible_with_relaxations():
    """
    Two must-do tasks needing 120min total, but only 60min capacity today and
    both due today → infeasible, with relaxations explaining how to fit.
    """
    t1 = _task("t1", 60, deadline=D0, must_do=True)
    t2 = _task("t2", 60, deadline=D0, must_do=True)
    r = solve_schedule(
        [t1, t2], [], [D0], _curves((D0, FLAT)),
        SolverConstraints(work_start_hour=8, work_end_hour=9),  # 60 min capacity
    )
    assert r.status == SolveStatus.infeasible
    assert r.bottleneck is not None
    assert r.bottleneck.deficit_min > 0
    # At least one relaxation should be offered (extend hours frees the 2nd task)
    assert len(r.relaxations) >= 1
    assert any(rl.frees_min > 0 for rl in r.relaxations)


def test_extend_hours_relaxation_is_offered_when_it_helps():
    t1 = _task("t1", 60, deadline=D0, must_do=True)
    t2 = _task("t2", 60, deadline=D0, must_do=True)
    r = solve_schedule(
        [t1, t2], [], [D0], _curves((D0, FLAT)),
        SolverConstraints(work_start_hour=8, work_end_hour=9),
    )
    actions = [rl.action for rl in r.relaxations]
    assert any("工作时段" in a for a in actions)


# ─── fixed blocks are routed around ─────────────────────────────────────────

def test_fixed_block_is_avoided():
    fixed = [FixedInterval(
        start=datetime(2026, 6, 15, 8, 0), end=datetime(2026, 6, 15, 9, 0),
    )]
    r = solve_schedule(
        [_task("t1", 60)], fixed, [D0], _curves((D0, FLAT)),
        SolverConstraints(work_start_hour=8, work_end_hour=11),
    )
    assert r.status == SolveStatus.feasible
    p = r.placements[0]
    # Must not overlap the 8-9 fixed block
    assert p.start >= datetime(2026, 6, 15, 9, 0)


def test_deadline_respected_no_placement_after_deadline():
    """A task due D0 can't be placed on D1/D2 even if D0 is full → infeasible."""
    blocker = _task("blocker", 60, deadline=D0, must_do=True)
    due_today = _task("due", 60, deadline=D0, must_do=True)
    r = solve_schedule(
        [blocker, due_today], [], [D0, D1, D2],
        _curves((D0, FLAT), (D1, FLAT), (D2, FLAT)),
        SolverConstraints(work_start_hour=8, work_end_hour=9),  # only 1 fits on D0
    )
    assert r.status == SolveStatus.infeasible
    # The unplaced must-do can't spill to D1/D2 because its deadline is D0
    placed_days = {p.day for p in r.placements}
    assert placed_days <= {D0}
