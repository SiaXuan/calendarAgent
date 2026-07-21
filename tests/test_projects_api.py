"""
Project layer API + completion tracking (Phase 4 Step 1.4/1.5).

Calendar side effects are inert here: completed block_keys are not present in any
schedule_store, so set_block_completion writes only the completion record (its
source of truth) and skips the CalDAV promote.
"""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import storage
from main import app
from models.project import CompletionStatus, PlanSnapshotItem

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_caldav(monkeypatch):
    """Keep tests hermetic + fast: no real iCloud calls even if .env has creds."""
    import integrations.caldav_client as c
    monkeypatch.setattr(c, "_make_client", lambda: None)


def test_project_crud(clean_stores):
    r = client.post("/projects", json={"name": "Course", "deadline": "2026-09-01"})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert r.json()["name"] == "Course"

    assert client.get(f"/projects/{pid}").json()["deadline"] == "2026-09-01"
    assert len(client.get("/projects").json()) == 1

    r = client.patch(f"/projects/{pid}", json={"name": "Course 2", "status": "archived"})
    assert r.json()["name"] == "Course 2" and r.json()["status"] == "archived"

    assert client.get("/projects/missing").status_code == 404


def test_delete_project_purges_everything(clean_stores):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    # attach a task + completion + snapshot to the project
    from models.task import Task, Priority, CognitiveLoad
    storage.task_store["t1"] = Task(
        id="t1", title="X", priority=Priority.medium,
        cognitive_load=CognitiveLoad.medium, estimated_hours=2, project_id=pid,
    )
    storage.project_plan_store[pid] = [
        PlanSnapshotItem(block_key="t1::A", task_id="t1", title="A", content_hash="h")
    ]
    client.put("/completions/t1::A", json={"status": "done", "project_id": pid})

    r = client.delete(f"/projects/{pid}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert pid not in storage.project_store
    assert "t1" not in storage.task_store            # task purged
    assert pid not in storage.project_plan_store
    assert "t1::A" not in storage.completion_store   # completion forgotten


def test_completion_record_and_filter(clean_stores):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    client.put("/completions/k1", json={"status": "done", "project_id": pid, "title": "K1"})
    client.put("/completions/k2", json={"status": "done"})   # no project

    scoped = client.get("/completions", params={"project_id": pid}).json()["completions"]
    assert [c["block_key"] for c in scoped] == ["k1"]
    assert len(client.get("/completions").json()["completions"]) == 2

    # clearing = set pending → record removed
    client.put("/completions/k1", json={"status": "pending"})
    assert "k1" not in storage.completion_store


def test_complete_block_without_schedule_writes_record(clean_stores):
    r = client.post(
        "/schedule/2026-07-21/blocks/t1::Write report/complete",
        json={"done": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["done"] is True and body["found_block"] is False
    rec = storage.completion_store["t1::Write report"]
    assert rec.status == CompletionStatus.done and rec.scheduled_date == date(2026, 7, 21)


def test_schedule_changeset_endpoint(clean_stores):
    from datetime import datetime
    from models.schedule import BlockType, DaySchedule, TimeBlock
    from agents.calendar_writeback import _tag_key

    d = "2026-07-21"
    storage.schedule_store[date(2026, 7, 21)] = DaySchedule(
        date=date(2026, 7, 21), energy_curve=[0.5] * 24,
        blocks=[TimeBlock(
            start=datetime(2026, 7, 21, 9, 0), end=datetime(2026, 7, 21, 10, 0),
            block_type=BlockType.scheduled, task_id="t1", title="Write",
        )],
        unscheduled=[], health_summary="OK",
    )
    # No current events → the block is a create.
    r = client.post(f"/schedule/{d}/changeset", json={"current_events": []})
    assert r.status_code == 200
    cs = r.json()
    assert [s["title"] for s in cs["create"]] == ["Write"]

    # Same event already present → unchanged, nothing to do.
    r2 = client.post(f"/schedule/{d}/changeset", json={"current_events": [{
        "tag_key": _tag_key("t1::Write"), "title": "Write",
        "start": "2026-07-21T09:00:00", "end": "2026-07-21T10:00:00",
    }]})
    cs2 = r2.json()
    assert cs2["unchanged"] == 1 and not cs2["create"] and not cs2["update"]


def test_progress_and_heatmap(clean_stores):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    storage.project_plan_store[pid] = [
        PlanSnapshotItem(block_key="t::A", task_id="t", title="A",
                         suggested_date=date(2026, 7, 21), content_hash="h1"),
        PlanSnapshotItem(block_key="t::B", task_id="t", title="B",
                         suggested_date=date(2026, 7, 22), content_hash="h2"),
    ]
    client.post("/schedule/2026-07-21/blocks/t::A/complete", json={"done": True})

    prog = client.get(f"/projects/{pid}/progress").json()
    assert prog["total"] == 2 and prog["done"] == 1
    assert prog["by_day"]["2026-07-21"] == {"total": 1, "done": 1}

    hm = client.get("/completions/heatmap",
                    params={"from": "2026-07-01", "to": "2026-07-31"}).json()
    assert hm["counts"]["2026-07-21"] == 1
