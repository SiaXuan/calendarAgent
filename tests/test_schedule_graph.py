"""End-to-end test for graphs/schedule_graph.py — graph composition + run."""
import pytest

from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from graphs.schedule_graph import build_schedule_graph, run_schedule_graph
from models.schedule import BlockType
from models.task import CognitiveLoad, TaskKind
from models.user import Language


def test_graph_topology_has_expected_nodes():
    """The compiled graph should expose all seven schedule nodes."""
    graph = build_schedule_graph()
    expected = {
        "fetch_health", "fetch_calendar", "rank_tasks",
        "split_instant", "compute_meals", "schedule", "assemble",
    }
    actual = set(graph.nodes.keys()) - {"__start__"}
    assert expected <= actual


async def test_run_schedule_graph_end_to_end(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_snapshot, sample_date,
):
    """
    Full pipeline: snapshot + task → DaySchedule with at least one scheduled
    block matching the task. schedule_store is populated as a side effect.
    """
    from storage import health_store, schedule_store, task_store
    health_store[sample_date] = sample_snapshot
    task_store[sample_task.id] = sample_task

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id,
            title="Energy curve — design",
            estimated_minutes=60,
            cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
            suggested_date=sample_date,
        ),
    ]))

    schedule = await run_schedule_graph(sample_date)

    assert schedule.date == sample_date
    assert len(schedule.energy_curve) == 24
    # Fixed block from CalDAV stub
    assert any(b.title == "Lunch meeting" for b in schedule.blocks)
    # Scheduled block from our task
    scheduled = [b for b in schedule.blocks if b.block_type == BlockType.scheduled]
    assert len(scheduled) >= 1
    assert scheduled[0].task_id == sample_task.id
    assert scheduled[0].task_kind == TaskKind.analytical
    # Blocks are sorted by start time
    starts = [b.start for b in schedule.blocks]
    assert starts == sorted(starts)
    # schedule_store cached the result
    assert schedule_store[sample_date] is schedule


async def test_meals_are_inserted_exactly_once(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_date,
):
    """
    Regression: an earlier topology wired compute_meals to BOTH fetch_calendar
    and split_instant, which caused LangGraph to fire compute_meals twice and
    insert two lunch + two dinner blocks. Guard against that ever returning.
    """
    from storage import task_store
    task_store[sample_task.id] = sample_task

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Work",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ]))

    schedule = await run_schedule_graph(sample_date)
    meal_blocks = [b for b in schedule.blocks if b.block_type == BlockType.meal]
    # Exactly one lunch and one dinner — never two of each.
    assert len(meal_blocks) == 2
    # Distinct labels — the bug produced two with the same label
    labels = {b.title for b in meal_blocks}
    assert len(labels) == 2


async def test_run_schedule_graph_with_no_tasks(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync, sample_date,
):
    """Empty task store still produces a valid schedule (fixed + meals only)."""
    schedule = await run_schedule_graph(sample_date)
    assert schedule.date == sample_date
    # No scheduled blocks
    assert not any(b.block_type == BlockType.scheduled for b in schedule.blocks)
    # But meal blocks should appear
    assert any(b.block_type == BlockType.meal for b in schedule.blocks)


async def test_graph_handles_llm_error_gracefully(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_date,
):
    """LLM failure → heuristic fallback in task_agent → graph still completes."""
    from storage import task_store
    task_store[sample_task.id] = sample_task
    mock_sonnet.set_structured_error(RuntimeError("LLM down"))

    schedule = await run_schedule_graph(sample_date)
    # The heuristic produced at least one subtask, which the scheduler
    # placed (or unscheduled). Either way the graph completed without raising.
    placed = [b for b in schedule.blocks if b.block_type == BlockType.scheduled]
    placed_ids = {b.task_id for b in placed}
    unscheduled_ids = {s.parent_id for s in schedule.unscheduled}
    assert sample_task.id in (placed_ids | unscheduled_ids)
