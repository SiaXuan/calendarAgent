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
    client, mock_sonnet, mock_caldav, mock_reminders_sync, sample_task,
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


# ─── /chat route ────────────────────────────────────────────────────────────

def test_chat_404_when_no_schedule(client):
    r = client.post("/chat", json={"message": "tired", "date": "2026-05-15"})
    assert r.status_code == 404


def test_chat_invalid_date(client):
    r = client.post("/chat", json={"message": "tired", "date": "bogus"})
    assert r.status_code == 422


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


# ─── / root ─────────────────────────────────────────────────────────────────

def test_root_returns_status(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
