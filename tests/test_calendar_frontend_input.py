"""
Frontend-supplied calendar input (Phase 4 Step 2, docs/ARCHITECTURE.md §0).

When the caller passes `calendar_events`, fetch_calendar builds fixed blocks +
free windows from them with pure functions and never reads CalDAV. When absent,
the legacy CalDAV path still runs. These tests exercise the node directly and
end-to-end through POST /schedule/generate.
"""
from datetime import date

from fastapi.testclient import TestClient

from agents import calendar_agent
from agents.nodes import fetch_calendar_node
from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from main import app
from models.schedule import BlockType
from models.task import CognitiveLoad, TaskKind

D = date(2026, 7, 22)
client = TestClient(app)


async def test_supplied_events_build_fixed_blocks(clean_stores):
    events = [
        {"title": "Standup", "start": "2026-07-22T09:00:00", "end": "2026-07-22T09:30:00"},
        {"title": "Lunch", "start": "2026-07-22T12:00:00", "end": "2026-07-22T13:00:00"},
    ]
    patch = await fetch_calendar_node({"target_date": D, "calendar_events": events})
    assert [b.title for b in patch["fixed_blocks"]] == ["Standup", "Lunch"]
    assert all(b.block_type == BlockType.fixed for b in patch["fixed_blocks"])
    assert patch["free_windows"]   # gaps around the two meetings


async def test_supplied_events_never_touch_caldav(clean_stores, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("CalDAV must not be read when events are supplied")
    monkeypatch.setattr(calendar_agent, "fetch_fixed_blocks", boom)

    # Empty list is still "authoritative: nothing fixed today", not a fallback.
    patch = await fetch_calendar_node({"target_date": D, "calendar_events": []})
    assert patch["fixed_blocks"] == []
    assert len(patch["free_windows"]) == 1   # whole work day free


async def test_agent_tagged_event_excluded_from_fixed(clean_stores):
    # An event this agent wrote (tag in the notes) is NOT a fixed block — it gets
    # re-scheduled, so excluding it here prevents duplicate blocks.
    events = [
        {"title": "User meeting", "start": "2026-07-22T10:00:00", "end": "2026-07-22T11:00:00"},
        {"title": "Agent block", "start": "2026-07-22T14:00:00", "end": "2026-07-22T15:00:00",
         "description": "[agent-scheduled:dayflow:abc123]"},
    ]
    patch = await fetch_calendar_node({"target_date": D, "calendar_events": events})
    assert [b.title for b in patch["fixed_blocks"]] == ["User meeting"]


async def test_none_falls_back_to_caldav(clean_stores, mock_caldav):
    # No calendar_events key → legacy CalDAV path (stubbed) still runs.
    patch = await fetch_calendar_node({"target_date": D})
    assert any(b.title == "Lunch meeting" for b in patch["fixed_blocks"])


async def test_generate_endpoint_uses_supplied_calendar(
    clean_stores, mock_sonnet, mock_reminders_sync, sample_task,
):
    from storage import task_store
    task_store[sample_task.id] = sample_task
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Deep work",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical, suggested_date=date(2026, 5, 15),
        ),
    ]))

    r = client.post("/schedule/generate", json={
        "date": "2026-05-15",
        "calendar_events": [
            {"title": "Client call", "start": "2026-05-15T11:00:00",
             "end": "2026-05-15T12:00:00"},
        ],
    })
    assert r.status_code == 200
    titles = [b["title"] for b in r.json()["blocks"]]
    assert "Client call" in titles                 # supplied fixed event kept
    assert "Lunch meeting" not in titles           # CalDAV stub NOT consulted
