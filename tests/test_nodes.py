"""
Tests for agents/nodes.py — each LangGraph node tested in isolation.

External agents (task_agent, calendar_agent, scheduler_agent) are touched
through real code paths where they're pure-Python, mocked where they call
out (CalDAV / LLM).
"""
from datetime import datetime

import pytest

from agents import nodes
from agents.nodes import (
    apply_adjustment_node,
    assemble_node,
    compute_meals_node,
    fetch_calendar_node,
    fetch_health_node,
    rank_tasks_node,
    schedule_node,
    split_instant_node,
)
from models.schedule import BlockType, FreeWindow, TimeBlock
from models.task import CognitiveLoad, Subtask, TaskKind
from models.user import Language


# pytest.ini sets asyncio_mode = auto, so async tests run automatically.


# ─── fetch_health_node ──────────────────────────────────────────────────────

async def test_fetch_health_with_snapshot(clean_stores, sample_snapshot, sample_date):
    """Real snapshot → real energy curve from health_agent (rule-based)."""
    state = {
        "target_date": sample_date,
        "language": Language.en,
        "snapshot": sample_snapshot,
    }
    patch = await fetch_health_node(state)
    assert len(patch["energy_curve"]) == 24
    assert all(0.0 <= v <= 1.0 for v in patch["energy_curve"])
    assert patch["health_summary"]   # non-empty
    assert patch["sleep_end_hour"] == 6   # snapshot wakes at 06:30
    assert patch["sleep_start_hour"] == 23


async def test_fetch_health_without_snapshot_uses_defaults(clean_stores, sample_date):
    """No snapshot → default curve + boilerplate summary."""
    state = {
        "target_date": sample_date,
        "language": Language.en,
        "snapshot": None,
    }
    patch = await fetch_health_node(state)
    assert len(patch["energy_curve"]) == 24
    assert "default" in patch["health_summary"].lower()
    assert patch["sleep_end_hour"] == 7   # default wake
    assert patch["sleep_start_hour"] == 23


async def test_fetch_health_uses_cache_on_second_call(
    clean_stores, sample_snapshot, sample_date,
):
    """Second call with same date hits the cache, not health_agent."""
    state = {"target_date": sample_date, "language": Language.en, "snapshot": sample_snapshot}
    first = await fetch_health_node(state)
    # Mutate snapshot afterwards to prove cache is used
    state["snapshot"] = None
    second = await fetch_health_node(state)
    assert first["energy_curve"] == second["energy_curve"]
    assert first["health_summary"] == second["health_summary"]


# ─── fetch_calendar_node ────────────────────────────────────────────────────

async def test_fetch_calendar_returns_caldav_results(
    clean_stores, mock_caldav, sample_date,
):
    """Calendar fetch yields fixed_blocks + free_windows from the stub."""
    state = {"target_date": sample_date, "language": Language.en}
    patch = await fetch_calendar_node(state)
    assert len(patch["fixed_blocks"]) == 1
    assert patch["fixed_blocks"][0].title == "Lunch meeting"
    assert len(patch["free_windows"]) == 2


async def test_fetch_calendar_uses_cache(clean_stores, mock_caldav, sample_date):
    state = {"target_date": sample_date, "language": Language.en}
    first = await fetch_calendar_node(state)
    second = await fetch_calendar_node(state)
    # Identity test: cache returned the same list objects
    assert first["fixed_blocks"] is second["fixed_blocks"]


# ─── rank_tasks_node ────────────────────────────────────────────────────────

async def test_rank_tasks_with_instant_task(
    clean_stores, mock_sonnet, sample_instant_task, sample_date,
):
    """Instant task bypasses LLM and lands in subtasks as is_instant=True."""
    state = {
        "target_date": sample_date,
        "language": Language.en,
        "tasks": [sample_instant_task],
    }
    patch = await rank_tasks_node(state)
    assert len(patch["subtasks"]) == 1
    assert patch["subtasks"][0].is_instant is True


async def test_rank_tasks_applies_subtask_overrides(
    clean_stores, mock_sonnet, sample_task, sample_date,
):
    """Per-task override replaces LLM decomposition for the matching parent_id."""
    from storage import subtask_overrides
    subtask_overrides[sample_task.id] = [Subtask(
        parent_id=sample_task.id,
        title="Override block",
        cognitive_load=CognitiveLoad.deep,
        task_kind=TaskKind.analytical,
        estimated_minutes=45,
        suggested_date=sample_date,
    )]
    # LLM would still be called for the task, but override wins post-call.
    from agents.task_agent import _LLMSubtask, _LLMSubtaskList
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="LLM block",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))

    state = {
        "target_date": sample_date, "language": Language.en, "tasks": [sample_task],
    }
    patch = await rank_tasks_node(state)
    # Override replaces LLM output
    titles = [s.title for s in patch["subtasks"]]
    assert "Override block" in titles
    assert "LLM block" not in titles


# ─── split_instant_node ─────────────────────────────────────────────────────

def test_split_instant_separates_instant_from_regular(clean_stores, sample_date):
    instant = Subtask(
        parent_id="t1", title="Pay bill",
        cognitive_load=CognitiveLoad.light, task_kind=TaskKind.admin,
        estimated_minutes=5, suggested_date=sample_date, is_instant=True,
    )
    regular = Subtask(
        parent_id="t2", title="Deep work",
        cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
        estimated_minutes=60, suggested_date=sample_date, is_instant=False,
    )
    state = {
        "target_date": sample_date, "language": Language.en,
        "subtasks": [instant, regular],
    }
    patch = split_instant_node(state)
    assert len(patch["subtasks"]) == 1
    assert patch["subtasks"][0].title == "Deep work"
    assert len(patch["instant_subtasks"]) == 1
    assert len(patch["instant_blocks"]) == 1
    assert patch["instant_blocks"][0].block_type == BlockType.instant
    assert patch["instant_blocks"][0].task_kind == TaskKind.admin


def test_split_instant_filters_future_dated_instants(clean_stores, sample_date):
    """An instant due tomorrow shouldn't appear in today's instant_blocks."""
    from datetime import timedelta
    future_instant = Subtask(
        parent_id="t1", title="Future bill",
        cognitive_load=CognitiveLoad.light, task_kind=TaskKind.admin,
        estimated_minutes=5, suggested_date=sample_date + timedelta(days=1),
        is_instant=True,
    )
    state = {
        "target_date": sample_date, "language": Language.en,
        "subtasks": [future_instant],
    }
    patch = split_instant_node(state)
    assert patch["instant_subtasks"] == []
    assert patch["instant_blocks"] == []


# ─── compute_meals_node ─────────────────────────────────────────────────────

def test_compute_meals_inserts_meal_blocks(clean_stores, sample_date):
    """Day with no existing fixed blocks gets lunch + dinner blocks inserted."""
    state = {
        "target_date": sample_date,
        "language": Language.en,
        "fixed_blocks": [],
        "sleep_end_hour": 7,
        "sleep_start_hour": 23,
    }
    patch = compute_meals_node(state)
    meal_blocks = patch["meal_blocks"]
    assert len(meal_blocks) >= 1
    assert all(b.block_type == BlockType.meal for b in meal_blocks)
    # fixed_blocks should now include the meal blocks too (sorted)
    assert any(b.block_type == BlockType.meal for b in patch["fixed_blocks"])


# ─── schedule_node ──────────────────────────────────────────────────────────

def test_schedule_node_places_subtask_in_window(clean_stores, sample_date):
    """Given a window and a small subtask, scheduler places it."""
    subtask = Subtask(
        parent_id="t1", title="Half-hour work",
        cognitive_load=CognitiveLoad.medium, task_kind=TaskKind.analytical,
        estimated_minutes=30, suggested_date=sample_date,
    )
    window = FreeWindow(
        start_hour=9, end_hour=12, duration_minutes=180, energy_score=0.9,
    )
    state = {
        "target_date": sample_date,
        "language": Language.en,
        "subtasks": [subtask],
        "free_windows": [window],
        "fixed_blocks": [],
        "sleep_start_hour": 23,
        "energy_curve": [0.9] * 24,   # uniformly high — placement succeeds
    }
    patch = schedule_node(state)
    assert len(patch["scheduled_blocks"]) == 1
    block = patch["scheduled_blocks"][0]
    assert block.task_id == "t1"
    assert block.task_kind == TaskKind.analytical


# ─── assemble_node ──────────────────────────────────────────────────────────

def test_assemble_combines_and_sorts_blocks(clean_stores, sample_date):
    """Final DaySchedule has all blocks sorted by start time and is in schedule_store."""
    from storage import schedule_store
    fixed = TimeBlock(
        start=datetime(sample_date.year, sample_date.month, sample_date.day, 13, 0),
        end=datetime(sample_date.year, sample_date.month, sample_date.day, 14, 0),
        block_type=BlockType.fixed, title="Lunch meeting",
    )
    scheduled = TimeBlock(
        start=datetime(sample_date.year, sample_date.month, sample_date.day, 9, 0),
        end=datetime(sample_date.year, sample_date.month, sample_date.day, 10, 0),
        block_type=BlockType.scheduled, title="Work",
    )
    state = {
        "target_date": sample_date,
        "fixed_blocks": [fixed],
        "instant_blocks": [],
        "scheduled_blocks": [scheduled],
        "energy_curve": [0.5] * 24,
        "unscheduled": [],
        "health_summary": "good",
    }
    patch = assemble_node(state)
    schedule = patch["final_schedule"]
    assert schedule.date == sample_date
    # Sorted by start time
    starts = [b.start for b in schedule.blocks]
    assert starts == sorted(starts)
    # Cached in schedule_store
    assert schedule_store[sample_date] is schedule


# ─── apply_adjustment_node ──────────────────────────────────────────────────

async def test_apply_adjustment_removes_blocks_after_hour(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_date,
):
    """remove_blocks_after_hour=15 → no scheduled blocks past 15:00."""
    from agents.task_agent import _LLMSubtask, _LLMSubtaskList
    from storage import task_store

    task_store[sample_task.id] = sample_task
    # Seed nodes._calendar_cache as if a prior generation populated it
    import time
    from models.schedule import FreeWindow, TimeBlock
    nodes._calendar_cache[sample_date] = ([], [
        FreeWindow(start_hour=8, end_hour=22, duration_minutes=14 * 60),
    ], time.monotonic())

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Work",
            estimated_minutes=30, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ]))

    state = {
        "target_date": sample_date,
        "language": Language.en,
        "remove_blocks_after_hour": 15,
    }
    patch = await apply_adjustment_node(state)
    schedule = patch["final_schedule"]
    for block in schedule.blocks:
        if block.block_type.value == "scheduled":
            assert block.start.hour < 15, f"Block at {block.start} should have been removed"
