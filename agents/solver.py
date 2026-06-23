"""
Multi-day, priority-aware schedule solver (Phase: 核心闭环 S1).

Fixes the root flaw of the old single-day greedy `scheduler_agent.generate_schedule`:
- **Energy was a gate** (low energy → deep task dropped). Now energy is a SECONDARY
  modifier: it decides ordering (high-load tasks → high-energy windows) and
  inflates duration on low-energy days, but never blocks a must-do task.
- **Single-day vision**. Now places across a multi-day horizon: urgent/must-do
  first (earliest feasible day), non-urgent deferred to later days when today is full.

Pure function, no LLM, no I/O. Returns a SolveResult that is either feasible
(placements) or infeasible (bottleneck + relaxations). Relaxations = "what would
make it fit" — computed by re-solving with one constraint relaxed at a time.

This is the deterministic "muscle". The agent (Layer 2) decides the constraints
to pass in and what to do with infeasibility — that's where autonomy lives.
"""
from datetime import date, datetime, timedelta

from models.solver import (
    Bottleneck,
    FixedInterval,
    Placement,
    Relaxation,
    SolveResult,
    SolveStatus,
    SolverConstraints,
    SolverTask,
)
from models.task import CognitiveLoad

_SLOT_STEP_MIN = 15  # candidate start granularity

# Cognitive load → how much this task "wants" high energy (placement preference weight).
_ENERGY_PREFERENCE = {
    CognitiveLoad.deep: 1.0,    # strongly prefers high-energy slots
    CognitiveLoad.medium: 0.5,
    CognitiveLoad.light: 0.0,   # indifferent — happy in the afternoon trough
}

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _day_work_bounds(day: date, c: SolverConstraints) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, c.work_start_hour)
    if c.work_end_hour >= 24:
        end = datetime(day.year, day.month, day.day) + timedelta(days=1)
    else:
        end = datetime(day.year, day.month, day.day, c.work_end_hour)
    return start, end


def _free_intervals(
    day: date,
    fixed: list[FixedInterval],
    occupied: list[tuple[datetime, datetime]],
    c: SolverConstraints,
) -> list[list[datetime]]:
    """Work-hour intervals on `day` minus fixed blocks and already-placed tasks."""
    work_start, work_end = _day_work_bounds(day, c)
    busy = sorted(
        [(f.start, f.end) for f in fixed if f.start.date() == day]
        + [(s, e) for (s, e) in occupied if s.date() == day],
        key=lambda x: x[0],
    )
    intervals: list[list[datetime]] = []
    cursor = work_start
    for bstart, bend in busy:
        if bstart > cursor:
            intervals.append([cursor, min(bstart, work_end)])
        cursor = max(cursor, bend)
        if cursor >= work_end:
            break
    if cursor < work_end:
        intervals.append([cursor, work_end])
    return [iv for iv in intervals if iv[1] > iv[0]]


def _mean_work_energy(curve: list[float], c: SolverConstraints) -> float:
    hours = list(range(c.work_start_hour, min(c.work_end_hour, 24)))
    if not hours:
        return 0.5
    return sum(curve[h] for h in hours) / len(hours)


def _effective_duration(task: SolverTask, day_low_energy: bool, c: SolverConstraints) -> int:
    if day_low_energy and c.inflate_factor > 1.0:
        return int(round(task.duration_min * c.inflate_factor))
    return task.duration_min


def _candidate_slots(
    interval: list[datetime], needed_min: int,
) -> list[datetime]:
    """Every _SLOT_STEP_MIN-aligned start in [interval] that fits needed_min."""
    out: list[datetime] = []
    cursor = interval[0]
    last_start = interval[1] - timedelta(minutes=needed_min)
    while cursor <= last_start:
        out.append(cursor)
        cursor += timedelta(minutes=_SLOT_STEP_MIN)
    return out


def _sort_tasks(tasks: list[SolverTask]) -> list[SolverTask]:
    """must_do first, then deadline urgency, then priority, then heavier load first."""
    return sorted(
        tasks,
        key=lambda t: (
            0 if t.must_do else 1,
            t.deadline or date.max,
            _PRIORITY_ORDER.get(t.priority, 1),
            _ENERGY_PREFERENCE.get(t.cognitive_load, 0.5) * -1,  # heavier first
        ),
    )


def _place_one(
    task: SolverTask,
    horizon: list[date],
    fixed: list[FixedInterval],
    occupied: list[tuple[datetime, datetime]],
    energy_curves: dict[date, list[float]],
    c: SolverConstraints,
) -> Placement | None:
    """
    Find the best (day, start) for one task across the horizon.
    Hard constraints: within work hours, before deadline / must_be_before,
    after must_be_after, no overlap. Among valid slots, maximize an energy-match
    score, preferring earlier days for urgency.
    """
    before = c.must_be_before.get(task.id) or task.deadline
    after = c.must_be_after.get(task.id)

    best: tuple[float, datetime, date, float, bool] | None = None  # (score, start, day, energy, inflated)
    for day_idx, day in enumerate(horizon):
        if before is not None and day > before:
            continue
        if after is not None and day < after:
            continue
        curve = energy_curves.get(day, [0.5] * 24)
        low = _mean_work_energy(curve, c) < c.low_energy_threshold
        needed = _effective_duration(task, low, c)
        for interval in _free_intervals(day, fixed, occupied, c):
            for start in _candidate_slots(interval, needed):
                energy = curve[start.hour]
                pref = _ENERGY_PREFERENCE.get(task.cognitive_load, 0.5)
                # Score: energy match (weighted by load preference) minus a small
                # day-lateness penalty so urgent things land earlier.
                score = energy * pref - day_idx * 0.15 - (start.hour / 100.0)
                if best is None or score > best[0]:
                    best = (score, start, day, energy, low)
    if best is None:
        return None
    _, start, day, energy, low = best
    needed = _effective_duration(task, low, c)
    placement = Placement(
        task_id=task.id, title=task.title, day=day,
        start=start, end=start + timedelta(minutes=needed),
        cognitive_load=task.cognitive_load, task_kind=task.task_kind,
        energy_at_slot=round(energy, 3), inflated=(needed != task.duration_min),
    )
    return placement


def _run_greedy(
    tasks: list[SolverTask],
    horizon: list[date],
    fixed: list[FixedInterval],
    energy_curves: dict[date, list[float]],
    c: SolverConstraints,
) -> tuple[list[Placement], list[SolverTask]]:
    """Greedy placement; returns (placements, unplaced)."""
    placements: list[Placement] = []
    unplaced: list[SolverTask] = []
    occupied: list[tuple[datetime, datetime]] = []
    for task in _sort_tasks(tasks):
        p = _place_one(task, horizon, fixed, occupied, energy_curves, c)
        if p is None:
            unplaced.append(task)
            continue
        placements.append(p)
        occupied.append((p.start, p.end + timedelta(minutes=c.buffer_min)))
    return placements, unplaced


def solve_schedule(
    tasks: list[SolverTask],
    fixed_blocks: list[FixedInterval],
    horizon: list[date],
    energy_curves: dict[date, list[float]],
    constraints: SolverConstraints | None = None,
) -> SolveResult:
    """
    Place `tasks` across `horizon`. Returns feasible (placements) or infeasible
    (bottleneck + relaxations). Infeasible = at least one must_do task unplaced.
    """
    c = constraints or SolverConstraints()
    placements, unplaced = _run_greedy(tasks, horizon, fixed_blocks, energy_curves, c)

    unplaced_must = [t for t in unplaced if t.must_do]
    if not unplaced_must:
        # Non-must-do leftovers are fine (they roll forward); still feasible.
        return SolveResult(
            status=SolveStatus.feasible,
            placements=placements,
            overflow=[{"task_id": t.id, "title": t.title, "reason": "deferred past horizon"}
                      for t in unplaced],
        )

    # Infeasible: compute bottleneck + relaxations.
    bottleneck = _compute_bottleneck(tasks, horizon, fixed_blocks, c, unplaced_must)
    relaxations = _compute_relaxations(tasks, fixed_blocks, horizon, energy_curves, c, placements)
    return SolveResult(
        status=SolveStatus.infeasible,
        placements=placements,
        overflow=[{"task_id": t.id, "title": t.title, "reason": "no feasible slot before deadline"}
                  for t in unplaced],
        bottleneck=bottleneck,
        relaxations=relaxations,
    )


def _compute_bottleneck(
    tasks: list[SolverTask], horizon: list[date],
    fixed: list[FixedInterval], c: SolverConstraints,
    unplaced_must: list[SolverTask],
) -> Bottleneck:
    must_min = sum(t.duration_min for t in tasks if t.must_do)
    # Total work-hour capacity over horizon minus fixed blocks.
    capacity = 0
    for day in horizon:
        ws, we = _day_work_bounds(day, c)
        day_cap = int((we - ws).total_seconds() // 60)
        fixed_min = sum(
            int((f.end - f.start).total_seconds() // 60)
            for f in fixed if f.start.date() == day
        )
        capacity += max(0, day_cap - fixed_min)
    deficit = sum(t.duration_min for t in unplaced_must)
    return Bottleneck(
        must_do_min=must_min, capacity_min=capacity,
        deficit_min=deficit, binding="work_hours",
    )


def _placed_minutes(placements: list[Placement]) -> int:
    return sum(int((p.end - p.start).total_seconds() // 60) for p in placements)


def _compute_relaxations(
    tasks: list[SolverTask], fixed: list[FixedInterval], horizon: list[date],
    energy_curves: dict[date, list[float]], c: SolverConstraints,
    base_placements: list[Placement],
) -> list[Relaxation]:
    """Re-solve with one constraint relaxed at a time; report what each frees."""
    base = _placed_minutes(base_placements)
    out: list[Relaxation] = []

    # 1) Extend work hours by 2h/day.
    if c.work_end_hour < 24:
        ext = c.model_copy(update={"work_end_hour": min(24, c.work_end_hour + 2)})
        p, _ = _run_greedy(tasks, horizon, fixed, energy_curves, ext)
        freed = _placed_minutes(p) - base
        if freed > 0:
            out.append(Relaxation(
                action=f"工作时段延长到 {ext.work_end_hour}:00", frees_min=freed,
            ))

    # 2) Drop low-energy inflation.
    if c.inflate_factor > 1.0:
        noinf = c.model_copy(update={"inflate_factor": 1.0})
        p, _ = _run_greedy(tasks, horizon, fixed, energy_curves, noinf)
        freed = _placed_minutes(p) - base
        if freed > 0:
            out.append(Relaxation(
                action="深度任务不做低能量时长膨胀", frees_min=freed,
                cost="低精力时硬扛，可能效率打折",
            ))

    # 3) Allow deferring must-do past deadline (drop the before constraint).
    relaxed_tasks = [t.model_copy(update={"must_do": False, "deadline": None}) for t in tasks]
    nc = c.model_copy(update={"must_be_before": {}})
    p, _ = _run_greedy(relaxed_tasks, horizon, fixed, energy_curves, nc)
    freed = _placed_minutes(p) - base
    if freed > 0:
        out.append(Relaxation(
            action="把部分必做任务推到截止日之后", frees_min=freed,
            cost="会错过 deadline",
        ))

    return out
