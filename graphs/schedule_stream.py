"""
SSE adapter for the schedule graph.

The frontend expects 4 event types in order: `health` → `fixed` → `schedule` → `done`.
We use LangGraph's `astream(stream_mode="updates")` to get a `{node_name: patch}`
dict after each node completes, then map node names to SSE events.

This replaces the bespoke `orchestrator.stream_day_schedule()` async generator
with a graph-driven path so LangSmith traces capture the full SSE-mode run too.
"""
import logging
from datetime import date

from agents.nodes import sync_reminders_if_due
from api.preferences import get_current_prefs
from graphs.schedule_graph import build_schedule_graph
from graphs.state import ScheduleState
from models.schedule import TimeBlock
from models.task import Subtask
from storage import health_store, task_store

_log = logging.getLogger("dayflow")


def _block_json(b: TimeBlock) -> dict:
    d = b.model_dump(mode="json")
    d["start"] = b.start.isoformat()
    d["end"] = b.end.isoformat()
    if b.deadline:
        d["deadline"] = b.deadline.isoformat()
    return d


def _subtask_json(s: Subtask) -> dict:
    d = s.model_dump(mode="json")
    if s.deadline:
        d["deadline"] = s.deadline.isoformat()
    return d


async def stream_schedule_events(target_date: date):
    """
    Yield SSE event dicts as graph nodes complete:
      {"type": "health",    "energy_curve": [...], "health_summary": "..."}
      {"type": "fixed",     "blocks": [...]}
      {"type": "schedule",  "blocks": [...], "unscheduled": [...]}
      {"type": "done"}

    Note: this is a graph-driven stream, so the order depends on node completion
    times. The fan-out branches (fetch_health, fetch_calendar, rank_tasks) run
    concurrently — health usually wins because it has no I/O.
    """
    await sync_reminders_if_due()

    initial_state: ScheduleState = {
        "target_date": target_date,
        "language": get_current_prefs().language,
        "snapshot": health_store.get(target_date),
        "tasks": list(task_store.values()),
    }

    graph = build_schedule_graph()

    fired_fixed = False
    fired_schedule = False
    async for update in graph.astream(initial_state, stream_mode="updates"):
        # update is {node_name: {state_patch}}
        for node_name, patch in update.items():
            if node_name == "fetch_health":
                yield {
                    "type": "health",
                    "energy_curve": patch.get("energy_curve", []),
                    "health_summary": patch.get("health_summary", ""),
                }
            elif node_name == "fetch_calendar" and not fired_fixed:
                # Emit the raw fixed_blocks here (meals haven't merged in yet).
                blocks = patch.get("fixed_blocks", [])
                yield {"type": "fixed", "blocks": [_block_json(b) for b in blocks]}
                fired_fixed = True
            elif node_name == "assemble" and not fired_schedule:
                schedule = patch.get("final_schedule")
                if schedule is not None:
                    yield {
                        "type": "schedule",
                        "blocks": [_block_json(b) for b in schedule.blocks],
                        "unscheduled": [_subtask_json(s) for s in schedule.unscheduled],
                    }
                    fired_schedule = True

    yield {"type": "done"}
