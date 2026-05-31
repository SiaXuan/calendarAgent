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
                                          schedule
                                              ▼
                                          assemble
                                              ▼
                                            END

`compute_meals` depends only on `fetch_calendar` (it needs fixed_blocks) — NOT
on `split_instant`. Earlier we wired both branches into `compute_meals` to
"converge" them, but LangGraph fires a node once per incoming branch
completion, not once per super-step where all in-edges become ready. That
caused `compute_meals` to run twice and place lunch/dinner blocks twice.

Convergence happens at `schedule` instead, which legitimately needs subtasks
(from split_instant) AND free_windows-with-meals (from compute_meals).
"""
from datetime import date
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from agents.nodes import (
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
from models.schedule import DaySchedule
from storage import health_store, task_store


@lru_cache(maxsize=1)
def build_schedule_graph():
    """Compile and cache the schedule graph (only one compilation per process)."""
    graph = StateGraph(ScheduleState)

    graph.add_node("fetch_health", fetch_health_node)
    graph.add_node("fetch_calendar", fetch_calendar_node)
    graph.add_node("rank_tasks", rank_tasks_node)
    graph.add_node("split_instant", split_instant_node)
    graph.add_node("compute_meals", compute_meals_node)
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

    # `schedule` is the real convergence point — it needs subtasks AND the
    # meal-aware free_windows.
    graph.add_edge("compute_meals", "schedule")
    graph.add_edge("split_instant", "schedule")
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
