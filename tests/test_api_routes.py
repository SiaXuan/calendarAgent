"""
FastAPI route tests using TestClient.

These verify the HTTP contract: status codes, response shapes, error paths.
Heavier integration logic is covered by the graph/node tests; here we just
make sure routes are correctly wired to the new graph-based pipeline.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from main import app
from models.task import CognitiveLoad, TaskKind


@pytest.fixture
def client(clean_stores):
    """TestClient with a clean store + lifespan disabled."""
    with TestClient(app) as c:
        yield c


# ─── /schedule routes ───────────────────────────────────────────────────────

def test_get_schedule_404_when_not_generated(client):
    r = client.get("/schedule/2026-05-15")
    assert r.status_code == 404


def test_generate_schedule_invalid_date(client):
    r = client.post("/schedule/generate", json={"date": "not-a-date"})
    assert r.status_code == 422


def test_generate_schedule_happy_path(
    client, mock_sonnet, mock_calendar_snapshot, mock_reminders_sync, sample_task,
):
    """POST /schedule/generate returns a DaySchedule + populates schedule_store."""
    from storage import task_store
    task_store[sample_task.id] = sample_task

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Work",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))

    r = client.post("/schedule/generate", json={"date": "2026-05-15"})
    assert r.status_code == 200
    data = r.json()
    assert data["date"] == "2026-05-15"
    # No health seeded → empty curve + source 'none' (energy Step 1 behavior)
    assert data["energy_curve"] == []
    assert data["energy_source"] == "none"
    assert isinstance(data["blocks"], list)

    # Now GET /schedule/{date} should also return it
    r2 = client.get("/schedule/2026-05-15")
    assert r2.status_code == 200


def test_write_schedule_404_when_not_generated(client):
    r = client.post("/schedule/2026-05-15/write")
    assert r.status_code == 404


# ─── /chat route (retired 2026-09-07) ───────────────────────────────────────

def test_legacy_chat_route_is_retired(client):
    """
    `POST /chat` (the pre-ReAct fixed-schema path) is commented out in
    api/chat.py — every client uses /chat/agent.

    Worth asserting rather than just deleting the old tests: one of them checked
    "404 when no schedule for that date", which kept passing after the route
    was unregistered because a missing route 404s too. A test that can't tell
    "not found" from "not routed" isn't testing anything.
    """
    assert client.post("/chat", json={"message": "tired", "date": "2026-05-15"}).status_code == 404
    # A live route would 422 on a malformed date; an absent one 404s first.
    assert client.post("/chat", json={"message": "tired", "date": "bogus"}).status_code == 404
    # The replacement is registered.
    assert client.post("/chat/agent", json={"message": "x", "date": "bogus"}).status_code == 422


# ─── /tasks routes ──────────────────────────────────────────────────────────

def test_create_and_list_task(client):
    payload = {
        "title": "Test task",
        "description": None,
        "priority": "high",
        "cognitive_load": "deep",
        "estimated_hours": 2.0,
        "deadline": "2026-05-20",
    }
    r = client.post("/tasks", json=payload)
    assert r.status_code == 200
    task = r.json()
    assert task["title"] == "Test task"
    assert task["task_kind"] == "analytical"   # default

    # List should now show it
    r2 = client.get("/tasks")
    assert r2.status_code == 200
    assert any(t["id"] == task["id"] for t in r2.json())


def test_delete_task(client):
    payload = {
        "title": "Disposable", "description": None,
        "priority": "low", "cognitive_load": "light",
        "estimated_hours": 0.5, "deadline": None,
    }
    created = client.post("/tasks", json=payload).json()
    r = client.delete(f"/tasks/{created['id']}")
    assert r.status_code == 200

    # Listing no longer contains it
    listed = client.get("/tasks").json()
    assert not any(t["id"] == created["id"] for t in listed)


def test_delete_unknown_task_404(client):
    r = client.delete("/tasks/does-not-exist")
    assert r.status_code == 404


# ─── /preferences routes ────────────────────────────────────────────────────

def test_get_preferences_returns_current(client):
    r = client.get("/preferences")
    assert r.status_code == 200
    body = r.json()
    assert "language" in body
    assert "work_start" in body
    assert "work_end" in body


def test_patch_preferences_updates_field(client):
    r = client.patch("/preferences", json={"work_start": 9})
    assert r.status_code == 200
    assert r.json()["work_start"] == 9
    # Reset for other tests
    client.patch("/preferences", json={"work_start": 8})


# ─── /chat/agent/undo ───────────────────────────────────────────────────────

def test_undo_rejects_bad_date(client):
    r = client.post("/chat/agent/undo", json={"date": "15-06-2026"})
    assert r.status_code == 422


def test_undo_without_snapshot_returns_degraded(client):
    """Wiring check — the undo logic itself is covered in test_agent_run.py."""
    r = client.post("/chat/agent/undo", json={"date": "2026-06-15"})
    assert r.status_code == 200
    assert r.json()["terminal_state"] == "degraded"


# ─── / root ─────────────────────────────────────────────────────────────────

def test_root_returns_status(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
