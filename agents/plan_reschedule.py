"""
Deterministic date shifting for plan import (Phase 4).

The LLM reads a document's *structure* (each task's term week + due weekday) and
parses the user's instruction into an `ImportAdjustment`. The actual calendar
arithmetic lives here — LLMs are unreliable at "what date is the 10th Monday of
the term" or "which weekday is 2027-09-13", so we never let them compute shifted
dates. Pure functions, fully unit-testable.

Two shift models, applied per candidate that has a date:
  • term-week (preferred): new_date = week-1 Monday of the new term + (week_index-1)
    weeks + weekday. Needs term_start_date + the task's week_index.
  • year-shift (fallback): keep the task's Nth-weekday-of-month, recomputed in the
    target year (optionally snapped to an overridden weekday).
Plus a simple whole-plan `shift_weeks` nudge.
"""
from datetime import date, timedelta

from models.plan_import import CandidateTask, ImportAdjustment


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> date:
    """The `nth` (1-based) `weekday` (0=Mon) in `year`/`month`; clamps to the last
    occurrence if `nth` overflows the month."""
    first = date(year, month, 1)
    first_hit = first + timedelta(days=(weekday - first.weekday()) % 7)
    cand = first_hit + timedelta(weeks=nth - 1)
    if cand.month != month:                       # e.g. no 5th Monday → use the last
        cand = first_hit + timedelta(weeks=nth - 2)
    return cand


def _shift_one(c: CandidateTask, adj: ImportAdjustment) -> date | None:
    orig = c.explicit_date
    # Target weekday: explicit override, else the task's own due weekday, else the
    # weekday the original date already falls on.
    target_wd = adj.due_weekday
    if target_wd is None:
        target_wd = c.due_weekday
    if target_wd is None and orig is not None:
        target_wd = orig.weekday()

    # Term-week model — most faithful to "week 1 starts here, due every <weekday>".
    if adj.term_start_date is not None and c.week_index is not None:
        week1_monday = _monday_of(adj.term_start_date)
        base = week1_monday + timedelta(weeks=c.week_index - 1)
        return base + timedelta(days=target_wd or 0)

    # Year-shift fallback — keep the Nth-weekday-of-month cadence in the new year.
    if adj.target_year is not None and orig is not None:
        nth = (orig.day - 1) // 7 + 1
        wd = target_wd if target_wd is not None else orig.weekday()
        return _nth_weekday_of_month(adj.target_year, orig.month, wd, nth)

    # Whole-plan nudge.
    if adj.shift_weeks is not None and orig is not None:
        return orig + timedelta(weeks=adj.shift_weeks)

    return orig


def is_noop(adj: ImportAdjustment) -> bool:
    return (adj.target_year is None and adj.term_start_date is None
            and adj.due_weekday is None and adj.shift_weeks is None)


def apply_adjustment(
    candidates: list[CandidateTask], adjustment: ImportAdjustment | None,
) -> list[CandidateTask]:
    """Return candidates with `explicit_date` recomputed per the adjustment.
    A no-op adjustment (all fields None) returns them unchanged."""
    if adjustment is None or is_noop(adjustment):
        return candidates
    out: list[CandidateTask] = []
    for c in candidates:
        new_date = _shift_one(c, adjustment)
        out.append(c.model_copy(update={"explicit_date": new_date}) if new_date != c.explicit_date else c)
    return out
