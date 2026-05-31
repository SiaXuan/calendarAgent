"""
Tests for graphs/schedule_stream.py — SSE event emission order + dedup.

These cover the regression we hit during initial Phase A: LangGraph's
super-step batching had buffered the `health` event behind the slow
`fetch_calendar` branch, and the `schedule` event was being emitted twice.
"""
from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from graphs.schedule_stream import stream_schedule_events
from models.task import CognitiveLoad, TaskKind


async def test_event_order_is_health_then_fixed_then_schedule_then_done(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_snapshot, sample_date,
):
    """SSE must produce events in this exact order for the UI to render right."""
    from storage import health_store, task_store
    health_store[sample_date] = sample_snapshot
    task_store[sample_task.id] = sample_task

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Block",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ]))

    events = [e async for e in stream_schedule_events(sample_date)]
    event_types = [e["type"] for e in events]

    # Must end with done
    assert event_types[-1] == "done"
    # The sequence preserves: health before fixed, fixed before schedule
    assert event_types.index("health") < event_types.index("fixed")
    assert event_types.index("fixed") < event_types.index("schedule")


async def test_schedule_event_fires_exactly_once(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_snapshot, sample_date,
):
    """
    Regression guard: assemble can emit twice if the graph re-traverses.
    The streamer must dedup so the frontend doesn't render the timeline twice.
    """
    from storage import health_store, task_store
    health_store[sample_date] = sample_snapshot
    task_store[sample_task.id] = sample_task

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Block",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ]))

    events = [e async for e in stream_schedule_events(sample_date)]
    event_types = [e["type"] for e in events]
    assert event_types.count("schedule") == 1
    assert event_types.count("fixed") == 1
    assert event_types.count("health") == 1
    assert event_types.count("done") == 1


async def test_health_event_payload_shape(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_snapshot, sample_date,
):
    """Health event must include energy_curve (24 floats) + health_summary string."""
    from storage import health_store
    health_store[sample_date] = sample_snapshot
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[]))

    events = [e async for e in stream_schedule_events(sample_date)]
    health_evt = next(e for e in events if e["type"] == "health")

    assert len(health_evt["energy_curve"]) == 24
    assert all(0.0 <= v <= 1.0 for v in health_evt["energy_curve"])
    assert isinstance(health_evt["health_summary"], str)


async def test_schedule_event_payload_shape(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
    sample_task, sample_snapshot, sample_date,
):
    """Schedule event must include block list with ISO-string starts."""
    from storage import health_store, task_store
    health_store[sample_date] = sample_snapshot
    task_store[sample_task.id] = sample_task
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Block",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=sample_date,
        ),
    ]))

    events = [e async for e in stream_schedule_events(sample_date)]
    sched_evt = next(e for e in events if e["type"] == "schedule")

    assert "blocks" in sched_evt
    assert "unscheduled" in sched_evt
    # Every block has ISO-string start/end (not datetime objects)
    for block in sched_evt["blocks"]:
        assert isinstance(block["start"], str)
        assert isinstance(block["end"], str)
        # Verify the field the frontend depends on is present
        assert "task_kind" in block
