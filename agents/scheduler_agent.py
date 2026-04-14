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
from models.user import Language

_MEAL_LABELS: dict[Language, tuple[str, str]] = {
    Language.en:    ("Lunch break",  "Dinner break"),
    Language.zh_CN: ("午餐休息",      "晚餐休息"),
    Language.zh_TW: ("午餐休息",      "晚餐休息"),
    Language.ja:    ("昼食休憩",      "夕食休憩"),
}

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_LOAD_ORDER = {CognitiveLoad.deep: 0, CognitiveLoad.medium: 1, CognitiveLoad.light: 2}

# Minimum energy required to schedule a task of a given cognitive load.
# These are intentionally modest — the energy curve is already scaled down
# for poor sleep/low HRV, so absolute thresholds must leave room to schedule.
_ENERGY_THRESHOLD = {
    CognitiveLoad.deep:   0.45,
    CognitiveLoad.medium: 0.28,
    CognitiveLoad.light:  0.15,
}

_BUFFER_MINUTES = 10
_SLOT_STEP = 30  # granularity for scanning start times within a free window

# ── Meal break reference (circadian biology) ─────────────────────────────────
# Sources: Panda (2019) "Circadian physiology of metabolism" Science;
#          Leproult & Van Cauter (2010) JAMA; Pot et al. (2016) Proc Nutrition Soc
#
# Key principles used here:
#  - Cortisol Awakening Response peaks 30-45 min post-wake → suppresses hunger,
#    so first meal is naturally 1-2 h after waking.
#  - Optimal lunch: ~4-5 h post-wake (insulin sensitivity still high, cortisol
#    declining). Clock target: 12:00-14:00 for typical 7-8 am wake.
#  - Optimal dinner: ~10-12 h post-wake, before the melatonin onset window
#    (typically ~2 h before usual bedtime). Clock target: 17:30-20:00.
#  - Minimum gap between meals: 4 h (ghrelin cycle resets).
#  - Last meal ≥ 3 h before sleep (reduces sleep fragmentation, Gill & Panda 2015).
#  - Eating window ≤ 12 h associated with better metabolic/sleep outcomes.
#
# Scheduling rule:
#   If a fixed block (class/meeting) ends INSIDE the meal window, the meal
#   starts immediately after (natural transition + the user is already free).
#   Otherwise, use the clock-based default anchored to wake time.

_MEAL_DURATION_MIN = 50   # eating + decompression


def compute_meal_breaks(
    fixed_blocks: list[TimeBlock],
    target_date: date,
    sleep_end_hour: int = 7,
    sleep_start_hour: int = 23,
    language: Language = Language.en,
) -> list[tuple[datetime, datetime, str]]:
    """
    Return [(start, end, label)] for lunch and dinner breaks.
    Meal windows are excluded from task scheduling and shown in the timeline.
    """
    def dt(h: int, m: int = 0) -> datetime:
        return datetime(target_date.year, target_date.month, target_date.day, h, m)

    # Hard windows within which a meal can be placed
    LUNCH_WIN_START  = dt(11, 30)
    LUNCH_WIN_END    = dt(14,  0)
    DINNER_WIN_START = dt(17,  0)
    DINNER_WIN_END   = dt(20,  0)

    # 2 h before sleep is the last acceptable meal end (Gill & Panda 2015)
    meal_deadline = dt(sleep_start_hour) - timedelta(hours=2)

    # Default start times anchored to wake hour (clamped to window)
    raw_lunch  = dt(max(12, sleep_end_hour + 4))
    raw_dinner = dt(max(18, min(sleep_end_hour + 11, 19)))
    default_lunch  = max(LUNCH_WIN_START,  min(raw_lunch,  LUNCH_WIN_END  - timedelta(minutes=_MEAL_DURATION_MIN)))
    default_dinner = max(DINNER_WIN_START, min(raw_dinner, DINNER_WIN_END - timedelta(minutes=_MEAL_DURATION_MIN)))

    def anchor_from_fixed(win_start: datetime, latest_ok_start: datetime) -> datetime | None:
        """Return the latest fixed-block end that falls inside [win_start, latest_ok_start]."""
        candidates = [
            b.end for b in fixed_blocks
            if win_start <= b.end <= latest_ok_start
        ]
        return max(candidates) if candidates else None

    # ── Lunch ────────────────────────────────────────────────────────────────
    latest_lunch_start = LUNCH_WIN_END - timedelta(minutes=_MEAL_DURATION_MIN)
    fixed_anchor_lunch = anchor_from_fixed(LUNCH_WIN_START, latest_lunch_start)
    lunch_start = fixed_anchor_lunch if fixed_anchor_lunch is not None else default_lunch
    lunch_end   = lunch_start + timedelta(minutes=_MEAL_DURATION_MIN)

    # ── Dinner ───────────────────────────────────────────────────────────────
    min_dinner_start   = max(DINNER_WIN_START, lunch_end + timedelta(hours=4))
    latest_dinner_start = min(DINNER_WIN_END - timedelta(minutes=_MEAL_DURATION_MIN), meal_deadline - timedelta(minutes=_MEAL_DURATION_MIN))
    # Don't anchor dinner on the lunch block itself
    dinner_search_start = max(DINNER_WIN_START, lunch_end + timedelta(hours=3))
    fixed_anchor_dinner = anchor_from_fixed(dinner_search_start, latest_dinner_start)
    raw_dinner_start = fixed_anchor_dinner if fixed_anchor_dinner is not None else default_dinner
    dinner_start = max(raw_dinner_start, min_dinner_start)
    dinner_end   = dinner_start + timedelta(minutes=_MEAL_DURATION_MIN)

    # Clamp dinner to meal_deadline
    if dinner_end > meal_deadline:
        dinner_end   = meal_deadline
        dinner_start = dinner_end - timedelta(minutes=_MEAL_DURATION_MIN)

    lunch_label, dinner_label = _MEAL_LABELS.get(language, _MEAL_LABELS[Language.en])
    result = []
    if lunch_end > lunch_start and lunch_start >= LUNCH_WIN_START:
        result.append((lunch_start, lunch_end, lunch_label))
    if dinner_end > dinner_start and dinner_end <= DINNER_WIN_END + timedelta(hours=1):
        result.append((dinner_start, dinner_end, dinner_label))
    return result


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
        start = datetime(target_date.year, target_date.month, target_date.day, w.start_hour, w.start_minute)
        if w.end_hour >= 24:
            end = datetime(target_date.year, target_date.month, target_date.day) + timedelta(days=1)
        else:
            end = datetime(target_date.year, target_date.month, target_date.day, w.end_hour, w.end_minute)
        if end > start:
            intervals.append([start, end])

    scheduled_blocks: list[TimeBlock] = []
    unscheduled: list[Subtask] = []

    for subtask in sorted_tasks:
        threshold = _ENERGY_THRESHOLD[subtask.cognitive_load]

        # First pass: must meet energy threshold → scheduled
        result = _find_best_slot(
            intervals, subtask.estimated_minutes, energy_curve,
            threshold, sleep_start_hour, scheduled_blocks, target_date,
        )
        is_soft_fallback = False

        # Soft fallback: ignore threshold, pick highest-energy available slot → suggested
        if result is None:
            result = _find_best_slot(
                intervals, subtask.estimated_minutes, energy_curve,
                0.0, sleep_start_hour, scheduled_blocks, target_date,
            )
            is_soft_fallback = True

        if result is not None:
            interval_idx, block_start = result
            block_end = block_start + timedelta(minutes=subtask.estimated_minutes)
            pomodoro_count = max(1, subtask.estimated_minutes // 25)
            scheduled_blocks.append(TimeBlock(
                start=block_start,
                end=block_end,
                block_type=BlockType.suggested if is_soft_fallback else BlockType.scheduled,
                task_id=subtask.parent_id,
                title=subtask.title,
                cognitive_load=subtask.cognitive_load,
                notes=None,
                phase_label=subtask.phase_label,
                focus_minutes=25,
                break_minutes=5,
                pomodoro_count=pomodoro_count,
                deadline=subtask.deadline,
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
        # No *continuous* deep work > 90 min — only applies when there is less
        # than a 30-min break between the two sessions (i.e. truly back-to-back).
        # If there's a class or a real break in between, this constraint is lifted.
        if (
            prev.cognitive_load == CognitiveLoad.deep
            and gap < 30                                        # back-to-back (< 30 min break)
            and (end - prev.start).total_seconds() / 60 > 90   # combined > 90 min
        ):
            return False

    return True


def _priority_of(subtask: Subtask) -> int:
    return 1  # Subtasks don't carry priority; pre-sorted by task_agent.
