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

import calendar

from models.plan_import import CandidateTask, DeadlineChange, ImportAdjustment


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


def _computed_date(orig: date | None, c: CandidateTask, adj: ImportAdjustment) -> date | None:
    """The shifted date for one field whose original value is `orig`.

    The term-week model derives a date from `week_index` and so can produce one
    even when `orig` is None (a "Week 3" item with no stated date). The year-shift
    and week-nudge models transform an existing date, so they return None when
    `orig` is None — the caller decides whether to fabricate."""
    target_wd = adj.due_weekday if adj.due_weekday is not None else c.due_weekday

    # Term-week model — most faithful to "week 1 starts here, due every <weekday>".
    if adj.term_start_date is not None and c.week_index is not None:
        wd = target_wd if target_wd is not None else (orig.weekday() if orig else 0)
        return _monday_of(adj.term_start_date) + timedelta(weeks=c.week_index - 1, days=wd)

    if orig is None:
        return None
    wd = target_wd if target_wd is not None else orig.weekday()

    # Year-shift — keep the Nth-weekday-of-month cadence in the new year.
    if adj.target_year is not None:
        return _nth_weekday_of_month(adj.target_year, orig.month, wd, (orig.day - 1) // 7 + 1)

    # Whole-plan nudge.
    if adj.shift_weeks is not None:
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
        # Shift the fields the task actually has.
        new_date = _computed_date(c.explicit_date, c, adjustment) if c.explicit_date else None
        new_deadline = _computed_date(c.explicit_deadline, c, adjustment) if c.explicit_deadline else None
        # A dateless "Week N" item still gets a date from the term-week model.
        if c.explicit_date is None and c.explicit_deadline is None:
            new_date = _computed_date(None, c, adjustment)
        if new_date != c.explicit_date or new_deadline != c.explicit_deadline:
            out.append(c.model_copy(update={
                "explicit_date": new_date, "explicit_deadline": new_deadline}))
        else:
            out.append(c)
    return out


def _nearest_month_day(month: int, day: int, today: date) -> date:
    """The occurrence of month/day closest to today, in whichever year that is.

    Nearest rather than next: a syllabus pasted mid-term lists dates already
    behind us ("9/14" in October is last month, not next September), while a
    request made in October for "1月15号" means the coming January. Day is
    clamped to the month's length, so "2月30号" lands on its last day.
    """
    def on(year: int) -> date:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    candidates = [on(today.year - 1), on(today.year), on(today.year + 1)]
    return min(candidates, key=lambda d: (abs((d - today).days), d < today))


def resolve_deadline_change(
    change: DeadlineChange | None, current: date | None, today: date
) -> date | None:
    """The deadline a described change produces, or None if it can't be computed.

    `current` is the task's existing deadline (needed only for shift_days);
    `today` anchors weekday and month/day requests. None means "no change could
    be derived" — the caller keeps whatever the task already had.
    """
    if change is None:
        return None
    if change.month is not None and change.day is not None:
        return _nearest_month_day(change.month, change.day, today)
    if change.weekday is not None:
        if change.weeks_from_now is None:
            # "周五": the next one, today included.
            return today + timedelta(days=(change.weekday - today.weekday()) % 7)
        return _monday_of(today) + timedelta(weeks=change.weeks_from_now, days=change.weekday)
    if change.shift_days is not None and current is not None:
        return current + timedelta(days=change.shift_days)
    return None
