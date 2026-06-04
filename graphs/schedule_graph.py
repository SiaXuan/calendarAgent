"""
LangGraph: full day schedule generation.

Topology:

    START
      ▼
    fetch_health
      │
      ├──► fetch_calendar ──► compute_meals ─┐
      │                                       │
      └──► rank_tasks ──► split_instant ──────┤
                                              ▼
                                          apply_pins
                                              ▼
                                          schedule
                                              ▼
                                          assemble
                                              ▼
                                            END

`compute_meals` depends only on `fetch_calendar` (single in-edge → fires once).
`apply_pins` and `schedule` each have two in-edges, but both incoming branches
are the SAME depth from START (compute_meals at 3, split_instant at 3), so
they land in the same super-step and the node fires exactly once. The earlier
duplicate-fire bug happened because compute_meals had in-edges from different
depths — keep this invariant in mind when adding nodes.

`apply_pins` converts user-pinned subtasks (set by drag-to-move + pomodoro +/-)
into TimeBlocks and merges them into fixed_blocks before the scheduler runs,
so the scheduler treats them as immovable.
"""
from datetime import date, timedelta
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from agents import calendar_agent, nodes, scheduler_agent
from agents.nodes import (
    apply_pins_node,
    assemble_node,
    compute_meals_node,
    fetch_calendar_node,
    fetch_health_node,
    rank_tasks_node,
    schedule_node,
    split_instant_node,
    sync_reminders_if_due,
)
from api.preferences import get_current_prefs
from graphs.state import ScheduleState
from models.schedule import BlockType, DaySchedule, TimeBlock
from models.task import CognitiveLoad, Subtask, TaskKind
from storage import health_store, schedule_store, subtask_pins, task_store


@lru_cache(maxsize=1)
def build_schedule_graph():
    """Compile and cache the schedule graph (only one compilation per process)."""
    graph = StateGraph(ScheduleState)

    graph.add_node("fetch_health", fetch_health_node)
    graph.add_node("fetch_calendar", fetch_calendar_node)
    graph.add_node("rank_tasks", rank_tasks_node)
    graph.add_node("split_instant", split_instant_node)
    graph.add_node("compute_meals", compute_meals_node)
    graph.add_node("apply_pins", apply_pins_node)
    graph.add_node("schedule", schedule_node)
    graph.add_node("assemble", assemble_node)

    # Health runs first (cheap, rule-based) so SSE can emit it immediately,
    # then calendar + rank_tasks fan out in parallel.
    # LangGraph's `stream_mode="updates"` only emits at super-step boundaries,
    # so if health were in the fan-out it would be buffered until the slower
    # branches (CalDAV) finish — visibly bad UX on /schedule/stream.
    graph.add_edge(START, "fetch_health")
    graph.add_edge("fetch_health", "fetch_calendar")
    graph.add_edge("fetch_health", "rank_tasks")

    # compute_meals has ONE in-edge (fetch_calendar). Putting it after both
    # fetch_calendar AND split_instant made it fire twice — see the topology
    # docstring above.
    graph.add_edge("rank_tasks", "split_instant")
    graph.add_edge("fetch_calendar", "compute_meals")

    # `apply_pins` reshapes (subtasks, fixed_blocks, free_windows) so the
    # scheduler sees pinned subtasks as fixed blocks. Must run AFTER split_instant
    # (it reads subtasks) AND AFTER compute_meals (it edits fixed_blocks + free_windows).
    graph.add_edge("compute_meals", "apply_pins")
    graph.add_edge("split_instant", "apply_pins")
    graph.add_edge("apply_pins", "schedule")
    graph.add_edge("schedule", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile()


async def run_schedule_graph(target_date: date) -> DaySchedule:
    """
    Entry point that mirrors the old `orchestrator.generate_day_schedule(date)`.

    Loads tasks + snapshot + language from the live stores, kicks off the
    throttled reminder sync, then invokes the compiled graph.
    """
    await sync_reminders_if_due()

    initial_state: ScheduleState = {
        "target_date": target_date,
        "language": get_current_prefs().language,
        "snapshot": health_store.get(target_date),
        "tasks": list(task_store.values()),
    }
    final_state = await build_schedule_graph().ainvoke(initial_state)
    return final_state["final_schedule"]


async def reflow_after_pin(target_date: date) -> DaySchedule:
    """
    Lightweight rescheduler triggered by pin/unpin (drag-to-move + pomodoro +/-).

    Why a separate path? The full schedule_graph runs ~10-30s because of
    AppleScript reminder sync, CalDAV fetch, health translation, and the LLM
    task decomposition. A pin operation doesn't need any of those — the set of
    tasks, calendar events, and energy curve are all unchanged. We just need to
    re-run the scheduler with the new pin in place.

    Reconstructs subtasks from the *existing* schedule_store[target_date] and
    reuses cached health + free windows. If no prior schedule exists, falls
    back to the full graph so first-time pins still work.

    Result is in-memory only (and saved to schedule_store) — caller doesn't
    need to re-fetch CalDAV or call any LLM.
    """
    current = schedule_store.get(target_date)
    if current is None:
        return await run_schedule_graph(target_date)

    # Split the current schedule into the three buckets the scheduler needs.
    fixed_blocks: list[TimeBlock] = []
    instant_blocks: list[TimeBlock] = []
    schedulable_subtasks: list[Subtask] = []
    for b in current.blocks:
        if b.block_type in (BlockType.fixed, BlockType.meal):
            fixed_blocks.append(b)
        elif b.block_type == BlockType.instant:
            instant_blocks.append(b)
        elif b.block_type in (BlockType.scheduled, BlockType.suggested) and b.task_id:
            duration = max(1, int((b.end - b.start).total_seconds() / 60))
            schedulable_subtasks.append(Subtask(
                parent_id=b.task_id,
                title=b.title,
                cognitive_load=b.cognitive_load or CognitiveLoad.medium,
                task_kind=b.task_kind or TaskKind.analytical,
                estimated_minutes=duration,
                suggested_date=target_date,
                deadline=b.deadline,
                phase_label=b.phase_label,
            ))

    # Apply pins → pinned subtasks become additional fixed blocks
    pins = subtask_pins.get(target_date, {})
    pin_blocks: list[TimeBlock] = []
    remaining_subtasks: list[Subtask] = []
    for s in schedulable_subtasks:
        key = f"{s.parent_id}::{s.title}"
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

    all_fixed = sorted(fixed_blocks + pin_blocks, key=lambda b: b.start)
    prefs = get_current_prefs()
    free_windows = calendar_agent.extract_free_windows(
        all_fixed, target_date, prefs.work_start, prefs.work_end,
    )

    # Reuse the cached energy curve from the last full generation.
    cached_health = nodes._health_cache.get(target_date)
    energy_curve = cached_health[0] if cached_health else nodes._default_energy_curve()
    health_summary = cached_health[1] if cached_health else current.health_summary

    # Sleep start hour: pull from snapshot if available, else default.
    snapshot = health_store.get(target_date)
    sleep_start_hour = 23
    if snapshot and snapshot.sleep.sleep_start.hour >= 20:
        sleep_start_hour = snapshot.sleep.sleep_start.hour

    result = scheduler_agent.generate_schedule(
        remaining_subtasks, free_windows, all_fixed, target_date,
        sleep_start_hour, energy_curve,
    )

    all_blocks = sorted(
        all_fixed + instant_blocks + result.blocks,
        key=lambda b: b.start,
    )
    new_schedule = DaySchedule(
        date=target_date,
        energy_curve=energy_curve,
        blocks=all_blocks,
        unscheduled=result.unscheduled,
        health_summary=health_summary,
    )
    schedule_store[target_date] = new_schedule
    return new_schedule
