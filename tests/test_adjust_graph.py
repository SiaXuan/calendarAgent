"""Tests for graphs/adjust_graph.py — chat-driven schedule adjustments."""
import time
from datetime import datetime

import pytest

from agents import nodes
from agents.chat_agent import AdjustmentParams
from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from graphs.adjust_graph import build_adjust_graph, run_adjust_graph
from models.schedule import BlockType, DaySchedule, FreeWindow, TimeBlock
from models.task import CognitiveLoad, TaskKind


def test_adjust_graph_topology():
    """Adjust graph is intentionally tiny — a single node + START + END."""
    graph = build_adjust_graph()
    assert "apply_adjustment" in graph.nodes


def _seed_schedule(target_date) -> DaySchedule:
    return DaySchedule(
        date=target_date,
        energy_curve=[0.5] * 24,
        blocks=[
            TimeBlock(
                start=datetime(target_date.year, target_date.month, target_date.day, 9, 0),
                end=datetime(target_date.year, target_date.month, target_date.day, 10, 0),
                block_type=BlockType.scheduled, title="Morning work",
            ),
        ],
        unscheduled=[],
        health_summary="ok",
    )


async def test_no_prior_schedule_returns_empty_params(clean_stores, mock_sonnet, sample_date):
    """If schedule_store is empty for the date, the graph short-circuits."""
    updated, params = await run_adjust_graph(sample_date, "clear afternoon")
    assert updated is None
    assert params.raw_intent == "clear afternoon"


async def test_clear_afternoon_removes_late_blocks(
    clean_stores, mock_sonnet, sample_task, sample_date,
):
    """User says 'clear afternoon' → no scheduled blocks past 15:00."""
    from storage import schedule_store, task_store

    schedule_store[sample_date] = _seed_schedule(sample_date)
    task_store[sample_task.id] = sample_task
    # Seed the calendar cache so apply_adjustment_node has a free window to schedule into.
    nodes._calendar_cache[sample_date] = ([], [
        FreeWindow(start_hour=8, end_hour=22, duration_minutes=14 * 60),
    ], time.monotonic())

    # First mock call: chat_agent → AdjustmentParams(remove_after=15)
    # Second mock call (same mock): task_agent → subtask list
    # Since both go through `with_structured_output`, we use a side_effect
    # via direct attribute manipulation: chat_agent runs first, then task_agent.
    chat_response = AdjustmentParams(remove_blocks_after_hour=15, raw_intent="clear afternoon")
    task_response = _LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Work",
            estimated_minutes=30, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ])

    # Toggle response based on call order
    responses = [chat_response, task_response]
    call_idx = {"i": 0}

    async def _ainvoke(messages, *args, **kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    mock_sonnet._structured.ainvoke = _ainvoke

    updated, params = await run_adjust_graph(sample_date, "clear afternoon")
    assert params.remove_blocks_after_hour == 15
    assert updated is not None
    # No scheduled block past 15:00
    for b in updated.blocks:
        if b.block_type == BlockType.scheduled:
            assert b.start.hour < 15


async def test_chat_error_returns_empty_adjustment(
    clean_stores, mock_sonnet, sample_date,
):
    """Chat LLM failure → empty AdjustmentParams; graph still runs adjust node."""
    from storage import schedule_store
    schedule_store[sample_date] = _seed_schedule(sample_date)
    nodes._calendar_cache[sample_date] = ([], [
        FreeWindow(start_hour=8, end_hour=22, duration_minutes=14 * 60),
    ], time.monotonic())

    mock_sonnet.set_structured_error(RuntimeError("chat LLM down"))

    updated, params = await run_adjust_graph(sample_date, "do something")
    # chat_agent catches the error and returns empty params with raw_intent.
    assert params.raw_intent == "do something"
    # The adjust graph still produces a DaySchedule (no-op adjustment).
    assert updated is not None
