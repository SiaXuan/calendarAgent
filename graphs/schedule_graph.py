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
from storage import (
    bump_schedule_version, health_store, schedule_store, subtask_pins, task_store,
)


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


async def run_schedule_graph(
    target_date: date, calendar_events: list[dict] | None = None,
) -> DaySchedule:
    """
    Entry point that mirrors the old `orchestrator.generate_day_schedule(date)`.

    Loads tasks + snapshot + language from the live stores, kicks off the
    throttled reminder sync, then invokes the compiled graph.

    `calendar_events` (docs/ARCHITECTURE.md §0): raw events the frontend read via
    EventKit for this day. None → the graph falls back to reading CalDAV (legacy).
    """
    await sync_reminders_if_due()

    # Full regenerate = fresh day → drop the old conversation + pending proposal.
    from storage import chat_sessions, pending_proposals
    chat_sessions.pop(target_date, None)
    pending_proposals.pop(target_date, None)

    initial_state: ScheduleState = {
        "target_date": target_date,
        "language": get_current_prefs().language,
        "snapshot": health_store.get(target_date),
        "tasks": list(task_store.values()),
        "calendar_events": calendar_events,
    }
    final_state = await build_schedule_graph().ainvoke(initial_state)
    return final_state["final_schedule"]


async def reflow_after_pin(target_date: date) -> DaySchedule:
    """
    Lightweight rescheduler triggered by pin/unpin (drag-to-move + pomodoro +/-).

    **Preserves every block's current position.** Only the pinned block(s) get
    resized/moved (per their PinSpec); then a forward cascade nudges any block
    that now overlaps. It does NOT re-run the greedy scheduler — doing so would
    relocate blocks the user (or the chat agent) deliberately placed (e.g.
    moving deep work back to the high-energy morning after the agent put it in
    the afternoon). Energy curve / health summary carry over unchanged.

    If no prior schedule exists, falls back to the full graph.
    """
    current = schedule_store.get(target_date)
    if current is None:
        return await run_schedule_graph(target_date)

    pins = subtask_pins.get(target_date, {})

    # Apply pins in place; keep every other block exactly where it is.
    updated: list[TimeBlock] = []
    for b in current.blocks:
        key = f"{b.task_id}::{b.title}" if b.task_id else None
        pin = pins.get(key) if key else None
        if pin is not None:
            updated.append(b.model_copy(update={
                "start": pin.start,
                "end": pin.start + timedelta(minutes=pin.duration_min),
                "pomodoro_count": max(1, pin.duration_min // 25),
            }))
        else:
            updated.append(b.model_copy(deep=True))

    cascaded = _cascade_in_place(updated)
    new_schedule = current.model_copy(update={
        "blocks": sorted(cascaded, key=lambda b: b.start),
    })
    schedule_store[target_date] = new_schedule
    bump_schedule_version(target_date)
    return new_schedule


_CASCADE_BUFFER = timedelta(minutes=10)
_MOVABLE_TYPES = {BlockType.scheduled, BlockType.suggested}


def _cascade_in_place(blocks: list[TimeBlock]) -> list[TimeBlock]:
    """
    Resolve overlaps by pushing later movable blocks forward, preserving each
    block's position when there's no conflict. fixed/meal/instant are immovable
    anchors; scheduled/suggested cascade around them.

    Only pushes forward — a block with a gap before it keeps its own start
    (gaps are not compacted). This mirrors the frontend's visual cascade so the
    persisted layout matches what the user saw.
    """
    anchors = sorted(
        ((b.start, b.end) for b in blocks if b.block_type not in _MOVABLE_TYPES),
        key=lambda x: x[0],
    )

    def clear_anchors(start, dur):
        # Push `start` forward until [start, start+dur) overlaps no anchor.
        changed = True
        while changed:
            changed = False
            for a_start, a_end in anchors:
                if start < a_end and start + dur > a_start:
                    start = a_end + _CASCADE_BUFFER
                    changed = True
        return start

    out: list[TimeBlock] = []
    cursor = None   # earliest free time as we walk left→right
    for b in sorted(blocks, key=lambda b: b.start):
        if b.block_type not in _MOVABLE_TYPES:
            out.append(b)
            end_plus = b.end + _CASCADE_BUFFER
            cursor = end_plus if cursor is None or end_plus > cursor else cursor
            continue
        dur = b.end - b.start
        start = b.start
        if cursor is not None and start < cursor:
            start = cursor            # previous block forces this one later
        start = clear_anchors(start, dur)
        end = start + dur
        out.append(b.model_copy(update={"start": start, "end": end}))
        cursor = end + _CASCADE_BUFFER
    return out
