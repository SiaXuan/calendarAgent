"""
LangGraph: chat-driven schedule adjustment.

This graph is intentionally tiny — a single node — because the adjustment
path reuses the cached health + calendar results from a prior schedule_graph
run. The point of using a graph here is consistency: the FastAPI route
invokes a graph just like the full-day path, and LangSmith traces stay
uniform across both flows.
"""
from datetime import date
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from agents.chat_agent import AdjustmentParams, handle_message
from agents.nodes import apply_adjustment_node
from api.preferences import get_current_prefs
from graphs.state import AdjustState
from models.schedule import DaySchedule
from storage import schedule_store


@lru_cache(maxsize=1)
def build_adjust_graph():
    graph = StateGraph(AdjustState)
    graph.add_node("apply_adjustment", apply_adjustment_node)
    graph.add_edge(START, "apply_adjustment")
    graph.add_edge("apply_adjustment", END)
    return graph.compile()


async def run_adjust_graph(
    target_date: date,
    user_message: str,
) -> tuple[DaySchedule, AdjustmentParams]:
    """
    Translate `user_message` into AdjustmentParams (via chat_agent), then run
    the adjustment graph to produce a new DaySchedule.
    """
    language = get_current_prefs().language
    current_schedule = schedule_store.get(target_date)
    if current_schedule is None:
        # No prior schedule — nothing to adjust. Bail with empty params.
        return current_schedule, AdjustmentParams(raw_intent=user_message)

    params = await handle_message(user_message, current_schedule, language)

    initial_state: AdjustState = {
        "target_date": target_date,
        "language": language,
        "user_message": user_message,
        "current_schedule": current_schedule,
        "energy_threshold_modifier": params.energy_threshold_modifier,
        "remove_blocks_after_hour": params.remove_blocks_after_hour,
        "reschedule_block_title": params.reschedule_block_title,
        "add_task_title": params.add_task_title,
        "add_task_load": params.add_task_load,
        "add_task_minutes": params.add_task_minutes,
    }
    final_state = await build_adjust_graph().ainvoke(initial_state)
    return final_state["final_schedule"], params
