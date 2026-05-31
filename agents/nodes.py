"""
LangGraph nodes that wrap the existing agent functions.

Each node takes the current ScheduleState (TypedDict from graphs.state) and
returns a *partial* dict — LangGraph merges those patches into the state.

This is the seam between the existing agent code (kept untouched) and the new
graph orchestration. When we delete orchestrator.py, this file is what holds
the wiring together.
"""
import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from agents import calendar_agent, health_agent, scheduler_agent, task_agent
from api.preferences import get_current_prefs
from models.schedule import BlockType, DaySchedule, FreeWindow, TimeBlock
from models.task import CognitiveLoad, Subtask
from storage import (
    health_store,
    schedule_store,
    subtask_overrides,
    task_store,
)

_log = logging.getLogger("dayflow")


# ─── Module-level caches mirroring orchestrator.py ──────────────────────────
# These keep the cache semantics intact during the migration. Once the
# orchestrator is deleted, nodes own the caches.
_health_cache: dict[date, tuple[list[float], str]] = {}
_calendar_cache: dict[date, tuple[list[TimeBlock], list[FreeWindow], float]] = {}
_CALENDAR_CACHE_TTL_S: float = 300.0

_calendar_lock = asyncio.Lock()

_last_sync_ts: float = 0.0
_SYNC_THROTTLE_S: float = 60.0


# ─── Pure helpers (lifted from orchestrator) ────────────────────────────────

def _apply_overrides(subtasks: list[Subtask]) -> list[Subtask]:
    """Replace subtasks with confirmed plans from task chat where available."""
    if not subtask_overrides:
        return subtasks
    result: list[Subtask] = []
    seen_overridden: set[str] = set()
    for s in subtasks:
        if s.parent_id in subtask_overrides:
            if s.parent_id not in seen_overridden:
                result.extend(subtask_overrides[s.parent_id])
                seen_overridden.add(s.parent_id)
        else:
            result.append(s)
    return result


def _make_instant_blocks(
    instant_subtasks: list[Subtask],
    target_date: date,
    work_start_hour: int,
) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    fallback_base = datetime(target_date.year, target_date.month, target_date.day, work_start_hour, 0)
    fallback_cursor = fallback_base

    for s in instant_subtasks:
        dt = s.due_datetime
        has_time = dt is not None and (dt.hour != 0 or dt.minute != 0)
        if has_time:
            start = datetime(target_date.year, target_date.month, target_date.day, dt.hour, dt.minute)
        else:
            start = fallback_cursor
            fallback_cursor += timedelta(minutes=6)
        end = start + timedelta(minutes=5)
        blocks.append(TimeBlock(
            start=start,
            end=end,
            block_type=BlockType.instant,
            task_id=s.parent_id,
            title=s.title,
            cognitive_load=CognitiveLoad.light,
            task_kind=s.task_kind,
            has_explicit_time=has_time,
        ))
    blocks.sort(key=lambda b: b.start)
    return blocks


def _default_energy_curve() -> list[float]:
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


# ─── Schedule graph nodes ───────────────────────────────────────────────────

async def fetch_health_node(state: dict) -> dict:
    """Compute energy curve + health summary from the snapshot in state."""
    target_date: date = state["target_date"]
    snapshot = state.get("snapshot")
    language = state["language"]

    cached = _health_cache.get(target_date)
    if cached:
        curve, summary = cached
    elif snapshot is None:
        curve = _default_energy_curve()
        summary = "No health data for today — using default energy curve."
    else:
        curve = health_agent.compute_energy_curve(snapshot)
        summary = await health_agent.get_health_summary(snapshot, language)
    _health_cache[target_date] = (curve, summary)

    sleep_end_hour = 7
    sleep_start_hour = 23
    if snapshot:
        if snapshot.sleep.sleep_end.hour >= 5:
            sleep_end_hour = snapshot.sleep.sleep_end.hour
        if snapshot.sleep.sleep_start.hour >= 20:
            sleep_start_hour = snapshot.sleep.sleep_start.hour

    return {
        "energy_curve": curve,
        "health_summary": summary,
        "sleep_end_hour": sleep_end_hour,
        "sleep_start_hour": sleep_start_hour,
    }


async def fetch_calendar_node(state: dict) -> dict:
    """Fetch fixed CalDAV blocks + initial free windows for the target date."""
    target_date: date = state["target_date"]
    prefs = get_current_prefs()

    cached = _calendar_cache.get(target_date)
    if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
        fixed_blocks, free_windows = cached[0], cached[1]
    else:
        async with _calendar_lock:
            cached = _calendar_cache.get(target_date)
            if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
                fixed_blocks, free_windows = cached[0], cached[1]
            else:
                try:
                    fixed_blocks, free_windows = await calendar_agent.fetch_fixed_blocks(
                        target_date, prefs.work_start, prefs.work_end
                    )
                except Exception as exc:
                    _log.warning("Calendar fetch failed: %s", exc)
                    if cached:
                        fixed_blocks, free_windows = cached[0], cached[1]
                    else:
                        fixed_blocks, free_windows = [], []
                if not free_windows:
                    free_windows = [FreeWindow(
                        start_hour=prefs.work_start,
                        end_hour=prefs.work_end,
                        duration_minutes=(prefs.work_end - prefs.work_start) * 60,
                    )]
                _calendar_cache[target_date] = (fixed_blocks, free_windows, time.monotonic())

    return {"fixed_blocks": fixed_blocks, "free_windows": free_windows}


async def rank_tasks_node(state: dict) -> dict:
    """Rank + decompose tasks via task_agent, apply per-task overrides."""
    tasks = state.get("tasks", [])
    target_date: date = state["target_date"]
    language = state["language"]

    all_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)
    all_subtasks = _apply_overrides(all_subtasks)
    return {"subtasks": all_subtasks}


def split_instant_node(state: dict) -> dict:
    """Split off instant subtasks and render them as TimeBlocks."""
    target_date: date = state["target_date"]
    all_subtasks = state.get("subtasks", [])
    prefs = get_current_prefs()

    instant_subtasks = [
        s for s in all_subtasks
        if s.is_instant and (s.suggested_date is None or s.suggested_date <= target_date)
    ]
    regular_subtasks = [s for s in all_subtasks if not s.is_instant]
    instant_blocks = _make_instant_blocks(instant_subtasks, target_date, prefs.work_start)
    return {
        "subtasks": regular_subtasks,
        "instant_subtasks": instant_subtasks,
        "instant_blocks": instant_blocks,
    }


def compute_meals_node(state: dict) -> dict:
    """Compute meal breaks + recompute free windows with meals excluded."""
    target_date: date = state["target_date"]
    fixed_blocks = state.get("fixed_blocks", [])
    sleep_end_hour = state.get("sleep_end_hour", 7)
    sleep_start_hour = state.get("sleep_start_hour", 23)
    language = state["language"]
    prefs = get_current_prefs()

    meal_windows = scheduler_agent.compute_meal_breaks(
        fixed_blocks, target_date, sleep_end_hour, sleep_start_hour, language
    )
    meal_blocks = [
        TimeBlock(
            start=start, end=end,
            block_type=BlockType.meal,
            title=label,
            cognitive_load=None,
        )
        for start, end, label in meal_windows
    ]
    all_fixed = sorted(fixed_blocks + meal_blocks, key=lambda b: b.start)
    free_windows_with_meals = calendar_agent.extract_free_windows(
        all_fixed, target_date, prefs.work_start, prefs.work_end
    )
    return {
        "meal_blocks": meal_blocks,
        "fixed_blocks": all_fixed,
        "free_windows": free_windows_with_meals,
    }


def schedule_node(state: dict) -> dict:
    """Run the deterministic scheduler over regular (non-instant) subtasks."""
    target_date: date = state["target_date"]
    subtasks = state.get("subtasks", [])
    free_windows = state.get("free_windows", [])
    fixed_blocks = state.get("fixed_blocks", [])
    sleep_start_hour = state.get("sleep_start_hour", 23)
    energy_curve = state.get("energy_curve", _default_energy_curve())

    result = scheduler_agent.generate_schedule(
        subtasks, free_windows, fixed_blocks, target_date,
        sleep_start_hour, energy_curve,
    )
    return {
        "scheduled_blocks": result.blocks,
        "unscheduled": result.unscheduled,
    }


def assemble_node(state: dict) -> dict:
    """Combine fixed + meal + instant + scheduled blocks into a DaySchedule."""
    target_date: date = state["target_date"]
    fixed_blocks = state.get("fixed_blocks", [])
    instant_blocks = state.get("instant_blocks", [])
    scheduled_blocks = state.get("scheduled_blocks", [])

    all_blocks = sorted(
        fixed_blocks + instant_blocks + scheduled_blocks,
        key=lambda b: b.start,
    )
    schedule = DaySchedule(
        date=target_date,
        energy_curve=state.get("energy_curve", _default_energy_curve()),
        blocks=all_blocks,
        unscheduled=state.get("unscheduled", []),
        health_summary=state.get("health_summary", ""),
    )
    schedule_store[target_date] = schedule
    return {"final_schedule": schedule}


# ─── Adjust graph node ──────────────────────────────────────────────────────

async def apply_adjustment_node(state: dict) -> dict:
    """
    Re-run the scheduler with adjustments. Reads health/calendar from the
    module caches populated by an earlier full generation.

    Lives outside the regular schedule graph because adjustments don't need
    the parallel fan-out — they reuse the cached results.
    """
    target_date: date = state["target_date"]
    language = state["language"]
    prefs = get_current_prefs()

    snapshot = health_store.get(target_date)
    cached_health = _health_cache.get(target_date)
    if cached_health:
        energy_curve, health_summary = cached_health
    elif snapshot:
        energy_curve = health_agent.compute_energy_curve(snapshot)
        health_summary = await health_agent.get_health_summary(snapshot, language)
    else:
        energy_curve = _default_energy_curve()
        health_summary = "No health data — using defaults."

    cached_calendar = _calendar_cache.get(target_date)
    fixed_blocks, free_windows = (
        (cached_calendar[0], cached_calendar[1]) if cached_calendar else ([], [])
    )

    scored_windows = health_agent.score_windows(free_windows, energy_curve)

    threshold_mod = state.get("energy_threshold_modifier", 0.0) or 0.0
    if threshold_mod != 0.0:
        scored_windows = [
            w.model_copy(update={"energy_score": max(0.0, w.energy_score + threshold_mod)})
            for w in scored_windows
        ]

    tasks = list(task_store.values())
    all_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)
    all_subtasks = _apply_overrides(all_subtasks)

    if state.get("add_task_title"):
        all_subtasks.append(Subtask(
            parent_id="adhoc",
            title=state["add_task_title"],
            cognitive_load=CognitiveLoad(state.get("add_task_load") or "light"),
            estimated_minutes=state.get("add_task_minutes") or 30,
            suggested_date=target_date,
        ))

    instant_subtasks = [
        s for s in all_subtasks
        if s.is_instant and (s.suggested_date is None or s.suggested_date <= target_date)
    ]
    regular_subtasks = [s for s in all_subtasks if not s.is_instant]
    instant_blocks = _make_instant_blocks(instant_subtasks, target_date, prefs.work_start)

    sleep_start_hour = 23
    if snapshot and snapshot.sleep.sleep_start.hour >= 20:
        sleep_start_hour = snapshot.sleep.sleep_start.hour

    result = scheduler_agent.generate_schedule(
        regular_subtasks, free_windows, fixed_blocks, target_date,
        sleep_start_hour, energy_curve,
    )

    filtered_blocks = result.blocks
    remove_after = state.get("remove_blocks_after_hour")
    if remove_after is not None:
        cutoff = datetime(target_date.year, target_date.month, target_date.day, remove_after, 0)
        filtered_blocks = [b for b in result.blocks if b.start < cutoff]

    all_blocks = sorted(fixed_blocks + instant_blocks + filtered_blocks, key=lambda b: b.start)
    schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=all_blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule_store[target_date] = schedule
    return {"final_schedule": schedule}


# ─── Pre-flight helper ──────────────────────────────────────────────────────

async def sync_reminders_if_due() -> None:
    """Best-effort throttled reminder sync. Called before generation graphs run."""
    global _last_sync_ts
    now = time.monotonic()
    if now - _last_sync_ts > _SYNC_THROTTLE_S:
        try:
            from api.tasks import do_sync_reminders
            await do_sync_reminders()
            _last_sync_ts = time.monotonic()
        except Exception:
            pass
