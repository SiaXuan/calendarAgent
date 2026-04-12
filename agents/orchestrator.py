"""
Orchestrator — coordinates all specialist agents and assembles the final DaySchedule.
Health, Calendar, and Task agents run concurrently via asyncio.gather.
"""
import asyncio
from datetime import date, datetime

from agents import calendar_agent, health_agent, scheduler_agent, task_agent
from agents.chat_agent import AdjustmentParams
from api.preferences import get_current_prefs
from models.health import HealthSnapshot
from models.schedule import BlockType, DaySchedule, FreeWindow, TimeBlock
from models.task import CognitiveLoad, Subtask, Task
from models.user import Language

# ── In-memory caches (Phase 1) ──────────────────────────────────────────────
_health_cache: dict[date, tuple[list[float], str]] = {}
_calendar_cache: dict[date, tuple[list[TimeBlock], list[FreeWindow]]] = {}

# Stored health snapshots keyed by date
health_store: dict[date, HealthSnapshot] = {}

# Stored tasks
task_store: dict[str, Task] = {}

# Stored schedules
schedule_store: dict[date, DaySchedule] = {}


async def generate_day_schedule(target_date: date) -> DaySchedule:
    """
    Full pipeline:
    1. Run Health + Calendar + Task agents concurrently
    2. Enrich free windows with energy scores
    3. Run Scheduler Agent
    4. Assemble and return DaySchedule
    """
    snapshot = health_store.get(target_date)
    tasks = list(task_store.values())
    language = get_current_prefs().language

    async def _run_health():
        if target_date in _health_cache:
            return _health_cache[target_date]
        if snapshot is None:
            curve = _default_energy_curve()
            summary = "No health data for today — using default energy curve."
        else:
            curve = health_agent.compute_energy_curve(snapshot)
            summary = await health_agent.get_health_summary(snapshot, language)
        result = (curve, summary)
        _health_cache[target_date] = result
        return result

    async def _run_calendar():
        if target_date in _calendar_cache:
            return _calendar_cache[target_date]
        prefs = get_current_prefs()
        fixed_blocks, free_windows = await calendar_agent.fetch_fixed_blocks(
            target_date, prefs.work_start, prefs.work_end
        )
        result = (fixed_blocks, free_windows)
        _calendar_cache[target_date] = result
        return result

    async def _run_tasks():
        return await task_agent.rank_and_decompose(tasks, target_date, language)

    (energy_curve, health_summary), (fixed_blocks, free_windows), ranked_subtasks = (
        await asyncio.gather(_run_health(), _run_calendar(), _run_tasks())
    )

    # Enrich free windows with energy scores
    scored_windows = health_agent.score_windows(free_windows, energy_curve)

    # Determine sleep start for constraint checks
    sleep_start_hour = 23
    if snapshot:
        sleep_start_hour = snapshot.sleep.sleep_start.hour

    # Run scheduler
    result = scheduler_agent.generate_schedule(
        ranked_subtasks, scored_windows, fixed_blocks, target_date, sleep_start_hour
    )

    schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=fixed_blocks + result.blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule.blocks.sort(key=lambda b: b.start)
    schedule_store[target_date] = schedule
    return schedule


async def apply_adjustment(
    target_date: date, params: AdjustmentParams
) -> DaySchedule:
    """
    Re-run only the Scheduler Agent using cached Health + Calendar data,
    applying adjustments from the Chat Agent.
    """
    snapshot = health_store.get(target_date)
    tasks = list(task_store.values())

    language = get_current_prefs().language

    cached_health = _health_cache.get(target_date)
    if cached_health:
        energy_curve, health_summary = cached_health
    else:
        if snapshot:
            energy_curve = health_agent.compute_energy_curve(snapshot)
            health_summary = await health_agent.get_health_summary(snapshot, language)
        else:
            energy_curve = _default_energy_curve()
            health_summary = "No health data — using defaults."

    cached_calendar = _calendar_cache.get(target_date)
    fixed_blocks, free_windows = cached_calendar if cached_calendar else ([], [])

    scored_windows = health_agent.score_windows(free_windows, energy_curve)

    # Apply energy threshold modifier
    if params.energy_threshold_modifier != 0.0:
        scored_windows = [
            w.model_copy(
                update={"energy_score": max(0.0, w.energy_score + params.energy_threshold_modifier)}
            )
            for w in scored_windows
        ]

    ranked_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)

    # Add ad-hoc task if requested
    if params.add_task_title:
        from models.task import CognitiveLoad, Subtask
        ranked_subtasks.append(
            Subtask(
                parent_id="adhoc",
                title=params.add_task_title,
                cognitive_load=CognitiveLoad(params.add_task_load or "light"),
                estimated_minutes=params.add_task_minutes or 30,
                suggested_date=target_date,
            )
        )

    sleep_start_hour = 23
    if snapshot:
        sleep_start_hour = snapshot.sleep.sleep_start.hour

    result = scheduler_agent.generate_schedule(
        ranked_subtasks, scored_windows, fixed_blocks, target_date, sleep_start_hour
    )

    # Filter blocks if clearing afternoon
    filtered_blocks = result.blocks
    if params.remove_blocks_after_hour is not None:
        cutoff = datetime(
            target_date.year, target_date.month, target_date.day,
            params.remove_blocks_after_hour, 0
        )
        filtered_blocks = [b for b in result.blocks if b.start < cutoff]

    schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=sorted(fixed_blocks + filtered_blocks, key=lambda b: b.start),
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule_store[target_date] = schedule
    return schedule


def _default_energy_curve() -> list[float]:
    """Reasonable default when no health data is available."""
    curve = [0.0] * 24
    for h in range(24):
        if 7 <= h <= 9:
            curve[h] = 0.7
        elif 10 <= h <= 12:
            curve[h] = 0.9
        elif 13 <= h <= 14:
            curve[h] = 0.6
        elif 15 <= h <= 17:
            curve[h] = 0.8
        elif 18 <= h <= 20:
            curve[h] = 0.6
        elif 21 <= h <= 22:
            curve[h] = 0.4
        else:
            curve[h] = 0.1
    return curve
