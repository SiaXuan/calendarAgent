"""
Orchestrator — coordinates all specialist agents and assembles the final DaySchedule.
Health, Calendar, and Task agents run concurrently via asyncio.gather.
"""
import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from agents import calendar_agent, health_agent, scheduler_agent, task_agent
from agents.chat_agent import AdjustmentParams
from api.preferences import get_current_prefs
from models.health import HealthSnapshot
from models.schedule import BlockType, DaySchedule, FreeWindow, TimeBlock
from models.task import CognitiveLoad, Subtask, Task
from models.user import Language

_DATA_DIR = Path(__file__).parent.parent / "data"
_HEALTH_FILE = _DATA_DIR / "health_store.json"
_log = logging.getLogger("dayflow")

# ── In-memory caches (Phase 1) ──────────────────────────────────────────────
_health_cache: dict[date, tuple[list[float], str]] = {}
# Calendar cache stores (fixed_blocks, free_windows, fetched_at_monotonic)
_calendar_cache: dict[date, tuple[list[TimeBlock], list[FreeWindow], float]] = {}
_CALENDAR_CACHE_TTL_S: float = 300.0  # 5-min TTL — long enough to cover any stream duration

# Serialise all CalDAV fetches: iCloud silently drops concurrent connections.
# Any second caller waits for the first to finish and then reuses the cached result.
_calendar_lock = asyncio.Lock()

# Throttle: don't re-sync reminders more than once per N seconds
_last_sync_ts: float = 0.0
_SYNC_THROTTLE_S: float = 60.0

# Stored health snapshots keyed by date
health_store: dict[date, HealthSnapshot] = {}

# Stored tasks
task_store: dict[str, Task] = {}

# Stored schedules
schedule_store: dict[date, DaySchedule] = {}

# Confirmed subtask plans from task chat (override Claude decomposition)
subtask_overrides: dict[str, list[Subtask]] = {}


# ── Health store persistence ─────────────────────────────────────────────────

def save_health_store() -> None:
    """Persist health_store to data/health_store.json."""
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {
            str(d): snapshot.model_dump(mode="json")
            for d, snapshot in health_store.items()
        }
        _HEALTH_FILE.write_text(json.dumps(payload, default=str))
    except Exception as exc:
        _log.warning("Could not save health store: %s", exc)


def load_health_store() -> None:
    """Load health_store from data/health_store.json on startup."""
    if not _HEALTH_FILE.exists():
        return
    try:
        payload = json.loads(_HEALTH_FILE.read_text())
        for date_str, data in payload.items():
            d = date.fromisoformat(date_str)
            health_store[d] = HealthSnapshot.model_validate(data)
        _log.info("Loaded %d health snapshot(s) from disk.", len(health_store))
    except Exception as exc:
        _log.warning("Could not load health store: %s", exc)


def _make_instant_blocks(
    instant_subtasks: list[Subtask],
    target_date: date,
    work_start_hour: int,
) -> list[TimeBlock]:
    """
    Convert instant subtasks to TimeBlocks.
    - If the reminder has an explicit time (hour != 0 or minute != 0), use that time.
    - If it's date-only (midnight), place at work_start and mark has_explicit_time=False.
    """
    blocks: list[TimeBlock] = []
    fallback_base = datetime(target_date.year, target_date.month, target_date.day, work_start_hour, 0)
    fallback_cursor = fallback_base

    for s in instant_subtasks:
        dt = s.due_datetime
        has_time = dt is not None and (dt.hour != 0 or dt.minute != 0)

        if has_time:
            # Use the reminder's actual due time (same day)
            start = datetime(target_date.year, target_date.month, target_date.day,
                             dt.hour, dt.minute)
        else:
            # Date-only reminder — slot at work_start sequentially
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
            has_explicit_time=has_time,
        ))

    # Sort by start time so timed reminders appear in chronological order
    blocks.sort(key=lambda b: b.start)
    return blocks


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


async def generate_day_schedule(target_date: date) -> DaySchedule:
    """
    Full pipeline:
    1. Run Health + Calendar + Task agents concurrently
    2. Enrich free windows with energy scores
    3. Run Scheduler Agent (regular tasks only — instant tasks bypass it)
    4. Assemble and return DaySchedule
    """
    global _last_sync_ts
    # Sync reminders at most once per throttle window so AppleScript doesn't
    # block every regeneration. Calendar is always re-fetched (fast, no AppleScript).
    now = time.monotonic()
    if now - _last_sync_ts > _SYNC_THROTTLE_S:
        try:
            from api.tasks import do_sync_reminders
            await do_sync_reminders()
            _last_sync_ts = now
        except Exception:
            pass  # Non-fatal — proceed with existing store

    # Always re-fetch health (cheap, no network).
    _health_cache.pop(target_date, None)

    snapshot = health_store.get(target_date)
    tasks = list(task_store.values())
    language = get_current_prefs().language
    prefs = get_current_prefs()

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
        # Fast path: fresh cache (no lock needed — just a read)
        cached = _calendar_cache.get(target_date)
        if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
            return cached[0], cached[1]
        # Serialize CalDAV calls — iCloud drops concurrent connections silently
        async with _calendar_lock:
            # Re-check inside lock: another coroutine may have fetched while we waited
            cached = _calendar_cache.get(target_date)
            if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
                return cached[0], cached[1]
            try:
                fixed_blocks, free_windows = await calendar_agent.fetch_fixed_blocks(
                    target_date, prefs.work_start, prefs.work_end
                )
            except Exception as exc:
                _log.warning("Calendar fetch failed: %s", exc)
                # Return stale cache rather than silently dropping events
                if cached:
                    return cached[0], cached[1]
                fixed_blocks, free_windows = [], []
            # If CalDAV returned no free windows, synthesise the full work day
            if not free_windows:
                free_windows = [FreeWindow(
                    start_hour=prefs.work_start,
                    end_hour=prefs.work_end,
                    duration_minutes=(prefs.work_end - prefs.work_start) * 60,
                )]
            _calendar_cache[target_date] = (fixed_blocks, free_windows, time.monotonic())
            return fixed_blocks, free_windows

    async def _run_tasks():
        all_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)
        return _apply_overrides(all_subtasks)

    (energy_curve, health_summary), (fixed_blocks, free_windows), all_subtasks = (
        await asyncio.gather(_run_health(), _run_calendar(), _run_tasks())
    )

    # Separate instant tasks from schedulable ones.
    # Instant reminders only surface on their due date (or if overdue/no deadline).
    instant_subtasks = [
        s for s in all_subtasks
        if s.is_instant and (s.suggested_date is None or s.suggested_date <= target_date)
    ]
    regular_subtasks = [s for s in all_subtasks if not s.is_instant]

    # Instant tasks become TimeBlocks at start of work day
    instant_blocks = _make_instant_blocks(instant_subtasks, target_date, prefs.work_start)

    # Score windows for display on the health card (not used for scheduling).
    scored_windows = health_agent.score_windows(free_windows, energy_curve)

    # Determine sleep hours (used for meal timing and scheduler constraints)
    sleep_end_hour = 7   # default wake time
    sleep_start_hour = 23
    if snapshot:
        if snapshot.sleep.sleep_end.hour >= 5:
            sleep_end_hour = snapshot.sleep.sleep_end.hour
        if snapshot.sleep.sleep_start.hour >= 20:
            sleep_start_hour = snapshot.sleep.sleep_start.hour

    # ── Meal breaks ───────────────────────────────────────────────────────────
    # Compute protected windows for lunch and dinner based on circadian biology
    # and the user's fixed schedule (e.g. class end shifts lunch start).
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
    # Re-compute free windows to exclude meal times from scheduling
    all_fixed_for_schedule = sorted(fixed_blocks + meal_blocks, key=lambda b: b.start)
    free_windows_with_meals = calendar_agent.extract_free_windows(
        all_fixed_for_schedule, target_date, prefs.work_start, prefs.work_end
    )

    # Run scheduler — passes energy_curve directly so it checks per-hour
    # energy at each candidate start time, not a coarse window average.
    result = scheduler_agent.generate_schedule(
        regular_subtasks, free_windows_with_meals, all_fixed_for_schedule, target_date,
        sleep_start_hour, energy_curve,
    )

    all_blocks = all_fixed_for_schedule + instant_blocks + result.blocks
    all_blocks.sort(key=lambda b: b.start)

    schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=all_blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
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
    prefs = get_current_prefs()

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
    fixed_blocks, free_windows = (cached_calendar[0], cached_calendar[1]) if cached_calendar else ([], [])

    scored_windows = health_agent.score_windows(free_windows, energy_curve)

    # Apply energy threshold modifier
    if params.energy_threshold_modifier != 0.0:
        scored_windows = [
            w.model_copy(
                update={"energy_score": max(0.0, w.energy_score + params.energy_threshold_modifier)}
            )
            for w in scored_windows
        ]

    all_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)
    all_subtasks = _apply_overrides(all_subtasks)

    # Add ad-hoc task if requested
    if params.add_task_title:
        all_subtasks.append(
            Subtask(
                parent_id="adhoc",
                title=params.add_task_title,
                cognitive_load=CognitiveLoad(params.add_task_load or "light"),
                estimated_minutes=params.add_task_minutes or 30,
                suggested_date=target_date,
            )
        )

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

    # Filter blocks if clearing afternoon
    filtered_blocks = result.blocks
    if params.remove_blocks_after_hour is not None:
        cutoff = datetime(
            target_date.year, target_date.month, target_date.day,
            params.remove_blocks_after_hour, 0
        )
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
    return schedule


async def stream_day_schedule(target_date: date):
    """
    Async generator that streams schedule-building progress as JSON dicts.
    Events (in order):
      {"type": "health",    "energy_curve": [...], "health_summary": "..."}
      {"type": "fixed",     "blocks": [...]}          # calendar events
      {"type": "schedule",  "blocks": [...], "unscheduled": [...]}  # full result
      {"type": "done"}

    Health is yielded FIRST (before any slow I/O) so the health card renders
    immediately. Reminder sync and CalDAV fetch happen after that.
    """
    global _last_sync_ts

    language = get_current_prefs().language
    prefs = get_current_prefs()
    snapshot = health_store.get(target_date)

    # ── Step 1: yield health immediately (no network needed) ─
    _health_cache.pop(target_date, None)

    if snapshot is None:
        energy_curve = _default_energy_curve()
        health_summary = "No health data for today — using default energy curve."
    else:
        energy_curve = health_agent.compute_energy_curve(snapshot)
        health_summary = await health_agent.get_health_summary(snapshot, language)
    _health_cache[target_date] = (energy_curve, health_summary)

    yield {"type": "health", "energy_curve": energy_curve, "health_summary": health_summary}

    # ── Step 2: sync reminders + calendar CONCURRENTLY ────────────────────
    # Both are I/O-bound; running in parallel cuts the wait to max(sync, cal)
    # instead of sync + cal.
    async def _do_sync():
        global _last_sync_ts
        now = time.monotonic()
        if now - _last_sync_ts > _SYNC_THROTTLE_S:
            try:
                from api.tasks import do_sync_reminders
                await do_sync_reminders()
                _last_sync_ts = time.monotonic()
            except Exception:
                pass

    async def _calendar_task():
        # Fast path: fresh cache (no lock needed — just a read)
        cached = _calendar_cache.get(target_date)
        if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
            return cached[0], cached[1]
        # Serialize CalDAV calls — iCloud drops concurrent connections silently
        async with _calendar_lock:
            cached = _calendar_cache.get(target_date)
            if cached and time.monotonic() - cached[2] < _CALENDAR_CACHE_TTL_S:
                return cached[0], cached[1]
            try:
                fb, fw = await calendar_agent.fetch_fixed_blocks(target_date, prefs.work_start, prefs.work_end)
            except Exception as exc:
                _log.warning("Calendar fetch failed in stream: %s", exc)
                if cached:
                    return cached[0], cached[1]
                fb, fw = [], []
            if not fw:
                fw = [FreeWindow(
                    start_hour=prefs.work_start, end_hour=prefs.work_end,
                    duration_minutes=(prefs.work_end - prefs.work_start) * 60,
                )]
            _calendar_cache[target_date] = (fb, fw, time.monotonic())
            return fb, fw

    # ── Step 2b: calendar fetch FIRST (sequential), then sync ────────────────
    # Running both concurrently caused iCloud to drop one CalDAV connection
    # silently, losing CS6140.  Sequential order: calendar → sync → tasks.
    fixed_blocks, free_windows = await _calendar_task()

    yield {"type": "fixed", "blocks": [_block_json(b) for b in fixed_blocks]}

    # Reminder sync runs AFTER calendar is fetched and cached — no concurrent CalDAV
    await _do_sync()

    # ── Step 3: task decomposition (Claude — slowest) ─────────────────────
    tasks = list(task_store.values())
    all_subtasks = await task_agent.rank_and_decompose(tasks, target_date, language)
    all_subtasks = _apply_overrides(all_subtasks)

    instant_subtasks = [
        s for s in all_subtasks
        if s.is_instant and (s.suggested_date is None or s.suggested_date <= target_date)
    ]
    regular_subtasks = [s for s in all_subtasks if not s.is_instant]

    sleep_end_hour = 7
    sleep_start_hour = 23
    if snapshot:
        if snapshot.sleep.sleep_end.hour >= 5:
            sleep_end_hour = snapshot.sleep.sleep_end.hour
        if snapshot.sleep.sleep_start.hour >= 20:
            sleep_start_hour = snapshot.sleep.sleep_start.hour

    instant_blocks = _make_instant_blocks(instant_subtasks, target_date, prefs.work_start)

    # ── Meal breaks ────────────────────────────────────────────────────────
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
    all_fixed_for_schedule = sorted(fixed_blocks + meal_blocks, key=lambda b: b.start)
    free_windows_with_meals = calendar_agent.extract_free_windows(
        all_fixed_for_schedule, target_date, prefs.work_start, prefs.work_end
    )

    result = scheduler_agent.generate_schedule(
        regular_subtasks, free_windows_with_meals, all_fixed_for_schedule, target_date,
        sleep_start_hour, energy_curve,
    )

    all_blocks = sorted(all_fixed_for_schedule + instant_blocks + result.blocks, key=lambda b: b.start)

    schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=all_blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule_store[target_date] = schedule

    yield {
        "type": "schedule",
        "blocks": [_block_json(b) for b in all_blocks],
        "unscheduled": [_subtask_json(s) for s in result.unscheduled],
    }
    yield {"type": "done"}


def _block_json(b: TimeBlock) -> dict:
    d = b.model_dump(mode="json")
    d["start"] = b.start.isoformat()
    d["end"] = b.end.isoformat()
    if b.deadline:
        d["deadline"] = b.deadline.isoformat()
    return d


def _subtask_json(s) -> dict:
    d = s.model_dump(mode="json")
    if s.deadline:
        d["deadline"] = s.deadline.isoformat()
    return d


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
