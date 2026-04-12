"""
Scheduler Agent — pure logic, no LLM call.

Design: energy_curve (24 floats, index = hour) is passed directly so the
scheduler checks the *actual energy at each candidate start time* rather than
a coarse window average.  Free windows from the calendar agent are treated
purely as availability boundaries (no-meeting zones); energy scoring lives
entirely in the energy curve.

Algorithm (per task):
  1. Scan every 30-min slot within every free window.
  2. Find the slot with the highest energy that meets the cognitive-load
     threshold AND passes hard constraints.
  3. If no threshold-meeting slot exists, fall back to the highest-energy
     available slot regardless of threshold (soft fallback).
  4. Repeat for remaining tasks.
"""
from datetime import date, datetime, timedelta

from models.schedule import BlockType, FreeWindow, ScheduleResult, TimeBlock
from models.task import CognitiveLoad, Subtask

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_LOAD_ORDER = {CognitiveLoad.deep: 0, CognitiveLoad.medium: 1, CognitiveLoad.light: 2}

# Minimum energy required to schedule a task of a given cognitive load
_ENERGY_THRESHOLD = {
    CognitiveLoad.deep:   0.65,
    CognitiveLoad.medium: 0.45,
    CognitiveLoad.light:  0.25,
}

_BUFFER_MINUTES = 10
_SLOT_STEP = 30  # granularity for scanning start times within a free window


def generate_schedule(
    subtasks: list[Subtask],
    windows: list[FreeWindow],
    fixed_blocks: list[TimeBlock],
    target_date: date,
    sleep_start_hour: int = 23,
    energy_curve: list[float] | None = None,
) -> ScheduleResult:
    """
    Place each subtask at the highest-energy available 30-min-granularity slot.
    Falls back to best-available if no slot meets the energy threshold.
    """
    if energy_curve is None or len(energy_curve) != 24:
        energy_curve = [0.5] * 24  # neutral fallback

    sorted_tasks = sorted(
        subtasks,
        key=lambda s: (_priority_of(s), _LOAD_ORDER[s.cognitive_load]),
    )

    # Each free window is represented as a mutable cursor [next_available, window_end].
    # Multiple tasks can consume the same window sequentially.
    intervals: list[list[datetime]] = []
    for w in windows:
        start = datetime(target_date.year, target_date.month, target_date.day, w.start_hour, 0)
        end   = datetime(target_date.year, target_date.month, target_date.day, w.end_hour,   0)
        if end > start:
            intervals.append([start, end])

    scheduled_blocks: list[TimeBlock] = []
    unscheduled: list[Subtask] = []

    for subtask in sorted_tasks:
        threshold = _ENERGY_THRESHOLD[subtask.cognitive_load]

        # First pass: must meet energy threshold
        result = _find_best_slot(
            intervals, subtask.estimated_minutes, energy_curve,
            threshold, sleep_start_hour, scheduled_blocks, target_date,
        )

        # Soft fallback: ignore threshold, pick highest-energy available slot
        if result is None:
            result = _find_best_slot(
                intervals, subtask.estimated_minutes, energy_curve,
                0.0, sleep_start_hour, scheduled_blocks, target_date,
            )

        if result is not None:
            interval_idx, block_start = result
            block_end = block_start + timedelta(minutes=subtask.estimated_minutes)
            pomodoro_count = max(1, round(subtask.estimated_minutes / 25))
            scheduled_blocks.append(TimeBlock(
                start=block_start,
                end=block_end,
                block_type=BlockType.scheduled,
                task_id=subtask.parent_id,
                title=subtask.title,
                cognitive_load=subtask.cognitive_load,
                notes=None,
                phase_label=subtask.phase_label,
                focus_minutes=25,
                break_minutes=5,
                pomodoro_count=pomodoro_count,
                is_uncertain=False,
            ))
            # Advance the cursor for this interval (task + buffer)
            intervals[interval_idx][0] = block_end + timedelta(minutes=_BUFFER_MINUTES)
        else:
            unscheduled.append(subtask)

    scheduled_blocks.sort(key=lambda b: b.start)
    return ScheduleResult(blocks=scheduled_blocks, unscheduled=unscheduled)


# ──────────────────────────────────────────────────────────────────────────────
# Slot finder
# ──────────────────────────────────────────────────────────────────────────────

def _find_best_slot(
    intervals: list[list[datetime]],
    needed_minutes: int,
    energy_curve: list[float],
    min_energy: float,
    sleep_start_hour: int,
    existing_blocks: list[TimeBlock],
    target_date: date,
) -> tuple[int, datetime] | None:
    """
    Scan all free intervals for the highest-energy start time that meets
    min_energy and passes hard constraints.  Step size: _SLOT_STEP minutes.
    Returns (interval_index, start_datetime) or None.
    """
    best_energy = -1.0
    best_result: tuple[int, datetime] | None = None

    for idx, (cursor, end_dt) in enumerate(intervals):
        slot = cursor
        while slot + timedelta(minutes=needed_minutes) <= end_dt:
            hour_energy = energy_curve[slot.hour]
            if hour_energy >= min_energy and hour_energy > best_energy:
                block_end = slot + timedelta(minutes=needed_minutes)
                if _check_constraints(slot, block_end, sleep_start_hour, existing_blocks, target_date):
                    best_energy = hour_energy
                    best_result = (idx, slot)
            slot += timedelta(minutes=_SLOT_STEP)

    return best_result


# ──────────────────────────────────────────────────────────────────────────────
# Hard constraints
# ──────────────────────────────────────────────────────────────────────────────

def _check_constraints(
    start: datetime,
    end: datetime,
    sleep_start_hour: int,
    existing: list[TimeBlock],
    target_date: date,
) -> bool:
    # No blocks within 1 h of tonight's sleep start
    sleep_boundary = (
        datetime(target_date.year, target_date.month, target_date.day, sleep_start_hour, 0)
        - timedelta(hours=1)
    )
    if start >= sleep_boundary:
        return False

    # No single sitting > 90 minutes
    if (end - start).total_seconds() / 60 > 90:
        return False

    # Buffer gap between consecutive blocks
    if existing:
        prev = existing[-1]
        gap = (start - prev.end).total_seconds() / 60
        if gap < _BUFFER_MINUTES:
            return False
        # No back-to-back deep work > 90 min total
        if (
            prev.cognitive_load == CognitiveLoad.deep
            and (end - prev.start).total_seconds() / 60 > 90
        ):
            return False

    return True


def _priority_of(subtask: Subtask) -> int:
    return 1  # Subtasks don't carry priority; pre-sorted by task_agent.
