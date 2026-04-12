"""
Scheduler Agent — pure logic, no LLM call.
Slots subtasks into free windows based on cognitive load match and priority.
"""
from datetime import date, datetime, timedelta

from models.health import HealthSnapshot
from models.schedule import BlockType, FreeWindow, ScheduleResult, TimeBlock
from models.task import CognitiveLoad, Priority, Subtask, Task

_PRIORITY_ORDER = {Priority.high: 0, Priority.medium: 1, Priority.low: 2}
_LOAD_ORDER = {CognitiveLoad.deep: 0, CognitiveLoad.medium: 1, CognitiveLoad.light: 2}

# Minimum energy required to schedule a task of a given cognitive load
_ENERGY_THRESHOLD = {
    CognitiveLoad.deep: 0.7,
    CognitiveLoad.medium: 0.5,
    CognitiveLoad.light: 0.3,
}

_BUFFER_MINUTES = 10


def generate_schedule(
    subtasks: list[Subtask],
    windows: list[FreeWindow],
    fixed_blocks: list[TimeBlock],
    target_date: date,
    sleep_start_hour: int = 23,
) -> ScheduleResult:
    """
    Greedy scheduler:
    1. Sort subtasks by priority DESC, then cognitive_load (deep first)
    2. Sort windows by energy_score DESC
    3. Match each subtask to the best eligible window
    4. Insert 10-minute buffers between consecutive blocks
    5. Hard constraints enforced
    """
    sorted_tasks = sorted(
        subtasks,
        key=lambda s: (_priority_of(s), _LOAD_ORDER[s.cognitive_load]),
    )
    sorted_windows = sorted(windows, key=lambda w: -w.energy_score)

    # Track remaining capacity per window (minutes)
    capacity: dict[int, int] = {
        i: w.duration_minutes for i, w in enumerate(sorted_windows)
    }
    # Track next available start time per window
    next_start: dict[int, datetime] = {}
    for i, w in enumerate(sorted_windows):
        next_start[i] = datetime(
            target_date.year, target_date.month, target_date.day, w.start_hour, 0
        )

    scheduled_blocks: list[TimeBlock] = []
    unscheduled: list[Subtask] = []

    for subtask in sorted_tasks:
        placed = False
        needed = subtask.estimated_minutes + _BUFFER_MINUTES

        for i, window in enumerate(sorted_windows):
            # Energy gate
            if window.energy_score < _ENERGY_THRESHOLD[subtask.cognitive_load]:
                continue

            # Capacity gate
            if capacity[i] < needed:
                continue

            block_start = next_start[i]
            block_end = block_start + timedelta(minutes=subtask.estimated_minutes)

            # Hard constraints
            if not _check_constraints(block_start, block_end, sleep_start_hour, scheduled_blocks, target_date):
                continue

            # Compute Pomodoro count from estimated duration
            pomodoro_count = max(1, round(subtask.estimated_minutes / 25))

            scheduled_blocks.append(
                TimeBlock(
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
                )
            )
            # Advance cursor (add buffer)
            next_start[i] = block_end + timedelta(minutes=_BUFFER_MINUTES)
            capacity[i] -= needed
            placed = True
            break

        if not placed:
            unscheduled.append(subtask)

    scheduled_blocks.sort(key=lambda b: b.start)
    return ScheduleResult(blocks=scheduled_blocks, unscheduled=unscheduled)


# ──────────────────────────────────────────────
# Constraint checks
# ──────────────────────────────────────────────

def _check_constraints(
    start: datetime,
    end: datetime,
    sleep_start_hour: int,
    existing: list[TimeBlock],
    target_date: date,
) -> bool:
    # No blocks within 1h of sleep_start
    sleep_boundary = datetime(
        target_date.year, target_date.month, target_date.day, sleep_start_hour, 0
    ) - timedelta(hours=1)
    if start >= sleep_boundary:
        return False

    # No single sitting > 90 minutes
    if (end - start).total_seconds() / 60 > 90:
        return False

    # No consecutive deep-work blocks > 90 minutes total
    if existing:
        prev = existing[-1]
        gap = (start - prev.end).total_seconds() / 60
        if gap < _BUFFER_MINUTES:
            return False
        if (
            prev.cognitive_load == CognitiveLoad.deep
            and (end - prev.start).total_seconds() / 60 > 90
        ):
            return False

    return True


def _priority_of(subtask: Subtask) -> int:
    """Map back from parent task — subtasks don't carry priority directly.
    Default to medium priority (1) since we don't have parent task here.
    The orchestrator should pre-sort subtasks by parent task priority.
    """
    return 1
