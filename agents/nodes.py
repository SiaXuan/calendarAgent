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
    bump_schedule_version,
    health_store,
    schedule_store,
    subtask_overrides,
    subtask_pins,
    task_store,
)


def _subtask_block_key(subtask: Subtask) -> str:
    """Mirror of the frontend's blockKey(): "{task_id}::{title}"."""
    return f"{subtask.parent_id}::{subtask.title}"

_log = logging.getLogger("dayflow")


# ─── Module-level caches mirroring orchestrator.py ──────────────────────────
# These keep the cache semantics intact during the migration. Once the
# orchestrator is deleted, nodes own the caches.
_health_cache: dict[date, tuple[list[float], str, str]] = {}   # (curve, summary, source)
_calendar_cache: dict[date, tuple[list[TimeBlock], list[FreeWindow], float]] = {}
_CALENDAR_CACHE_TTL_S: float = 300.0

_calendar_lock = asyncio.Lock()

_last_sync_ts: float = 0.0
_SYNC_THROTTLE_S: float = 60.0

# Scheduling window. Only work whose deadline falls within this many days enters
# scheduling at all (overdue + undated always in); far-deadline items (a syllabus
# assignment due in months) stay off the radar until their deadline approaches.
# The multi-day planner distributes an in-window task across the days up to its
# deadline so it gets done gradually rather than last-minute.
SCHEDULE_HORIZON_DAYS: int = 5


def _within_horizon(task, target_date: date) -> bool:
    if task.deadline is None:
        return True
    return task.deadline <= target_date + timedelta(days=SCHEDULE_HORIZON_DAYS)


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


# ─── Schedule graph nodes ───────────────────────────────────────────────────

async def fetch_health_node(state: dict) -> dict:
    """Compute energy curve + health summary from the snapshot in state."""
    target_date: date = state["target_date"]
    snapshot = state.get("snapshot")
    language = state["language"]

    cached = _health_cache.get(target_date)
    if cached:
        curve, summary, source = cached
    elif snapshot is None:
        # No log today → leave the curve EMPTY (scheduling runs energy-neutral)
        # and tell the UI not to draw a fake curve. Step 2 will slot a learned
        # baseline in here before falling through to "none".
        curve = []
        summary = ""
        source = "none"
    else:
        curve = health_agent.compute_energy_curve(snapshot)
        summary = await health_agent.get_health_summary(snapshot, language)
        source = "today"
    _health_cache[target_date] = (curve, summary, source)

    sleep_end_hour = 7
    sleep_start_hour = 23
    if snapshot:
        if snapshot.sleep.sleep_end.hour >= 5:
            sleep_end_hour = snapshot.sleep.sleep_end.hour
        if snapshot.sleep.sleep_start.hour >= 20:
            sleep_start_hour = snapshot.sleep.sleep_start.hour

    return {
        "energy_curve": curve,
        "energy_source": source,
        "health_summary": summary,
        "sleep_end_hour": sleep_end_hour,
        "sleep_start_hour": sleep_start_hour,
    }


def _free_windows_or_whole_day(free_windows, prefs):
    """A day with no gaps between fixed blocks still has the whole work day free."""
    if free_windows:
        return free_windows
    return [FreeWindow(
        start_hour=prefs.work_start,
        end_hour=prefs.work_end,
        duration_minutes=(prefs.work_end - prefs.work_start) * 60,
    )]


async def fetch_calendar_node(state: dict) -> dict:
    """
    Build the day's fixed blocks + free windows.

    Local/EventKit path (docs/ARCHITECTURE.md §0): when the caller supplies
    `calendar_events` (raw event dicts the frontend read via EventKit) we derive
    everything from those with pure functions — no network, no cache (the data is
    already request-fresh, and caching per date would leak across requests). When
    it's absent (None) we fall back to the legacy CalDAV fetch, retired in
    migration step 5.
    """
    target_date: date = state["target_date"]
    prefs = get_current_prefs()

    supplied = state.get("calendar_events")
    if supplied is not None:
        fixed_blocks = calendar_agent.events_to_fixed_blocks(supplied, target_date)
        free_windows = calendar_agent.extract_free_windows(
            fixed_blocks, target_date, prefs.work_start, prefs.work_end)
        return {
            "fixed_blocks": fixed_blocks,
            "free_windows": _free_windows_or_whole_day(free_windows, prefs),
        }

    # ── Legacy CalDAV fallback (no frontend calendar data supplied) ──
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
    """Rank + decompose tasks via task_agent, apply per-task overrides.

    Pulls memory bullets (Phase C.3) and threads them into the LLM prompt so
    the agent honours learned user patterns (e.g. "user prefers analytical
    work in mornings").
    """
    from memory import retrieval

    target_date: date = state["target_date"]
    # Only schedule work due within the lookahead window (+ overdue / undated) —
    # far-future items wait for their day instead of being crammed onto today.
    tasks = [t for t in state.get("tasks", []) if _within_horizon(t, target_date)]
    language = state["language"]
    memory_context = retrieval.for_task_ranking()

    all_subtasks = await task_agent.rank_and_decompose(
        tasks, target_date, language, memory_context=memory_context,
    )
    all_subtasks = _apply_overrides(all_subtasks)

    # Multi-day planner (Step 1.6): project work is distributed across days by the
    # planner, not decomposed here — inject today's allocated chunks alongside the
    # day's ad-hoc/reminder subtasks.
    from agents import project_service
    all_subtasks += project_service.chunk_subtasks_for_date(target_date)

    return {"subtasks": all_subtasks, "user_memory": memory_context}


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


def apply_pins_node(state: dict) -> dict:
    """
    Convert user-pinned subtasks into TimeBlocks and treat them as additional
    fixed blocks. The matching subtasks are removed from the scheduler's input
    so they don't get placed twice.

    Pin precedence: pinned subtasks have already had their final slot decided
    by the /schedule/{date}/pin endpoint (with conflict resolution baked in),
    so this node trusts the pin's start + duration verbatim.
    """
    target_date: date = state["target_date"]
    pins = subtask_pins.get(target_date, {})
    if not pins:
        return {}   # no-op patch

    subtasks: list[Subtask] = state.get("subtasks", [])
    fixed_blocks: list[TimeBlock] = list(state.get("fixed_blocks", []))
    free_windows: list[FreeWindow] = state.get("free_windows", [])
    prefs = get_current_prefs()

    pin_blocks: list[TimeBlock] = []
    remaining_subtasks: list[Subtask] = []

    for s in subtasks:
        key = _subtask_block_key(s)
        pin = pins.get(key)
        if pin is None:
            remaining_subtasks.append(s)
            continue
        pin_end = pin.start + timedelta(minutes=pin.duration_min)
        pin_blocks.append(TimeBlock(
            start=pin.start,
            end=pin_end,
            block_type=BlockType.scheduled,
            task_id=s.parent_id,
            title=s.title,
            cognitive_load=s.cognitive_load,
            task_kind=s.task_kind,
            phase_label=s.phase_label,
            focus_minutes=25,
            break_minutes=5,
            pomodoro_count=max(1, pin.duration_min // 25),
            deadline=s.deadline,
        ))

    if not pin_blocks:
        # User has pins recorded but none match the current subtask list —
        # likely the parent task was deleted. Silently drop.
        return {"subtasks": remaining_subtasks}

    new_fixed = sorted(fixed_blocks + pin_blocks, key=lambda b: b.start)
    new_free = calendar_agent.extract_free_windows(
        new_fixed, target_date, prefs.work_start, prefs.work_end,
    )
    return {
        "subtasks": remaining_subtasks,
        "fixed_blocks": new_fixed,
        "free_windows": new_free,
    }


def schedule_node(state: dict) -> dict:
    """Run the deterministic scheduler over regular (non-instant) subtasks."""
    target_date: date = state["target_date"]
    subtasks = state.get("subtasks", [])
    free_windows = state.get("free_windows", [])
    fixed_blocks = state.get("fixed_blocks", [])
    sleep_start_hour = state.get("sleep_start_hour", 23)
    energy_curve = state.get("energy_curve", [])   # empty → energy-neutral scheduling

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
        energy_curve=state.get("energy_curve", []),
        energy_source=state.get("energy_source", "none"),
        blocks=all_blocks,
        unscheduled=state.get("unscheduled", []),
        health_summary=state.get("health_summary", ""),
    )
    schedule_store[target_date] = schedule
    bump_schedule_version(target_date)   # invalidate any stale pending Proposal
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
        energy_curve, health_summary, energy_source = cached_health
    elif snapshot:
        energy_curve = health_agent.compute_energy_curve(snapshot)
        health_summary = await health_agent.get_health_summary(snapshot, language)
        energy_source = "today"
    else:
        energy_curve = []          # energy-neutral; UI won't draw a fake curve
        health_summary = ""
        energy_source = "none"

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

    tasks = [t for t in task_store.values() if _within_horizon(t, target_date)]
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
        energy_source=energy_source,
        blocks=all_blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule_store[target_date] = schedule
    bump_schedule_version(target_date)   # invalidate any stale pending Proposal
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
