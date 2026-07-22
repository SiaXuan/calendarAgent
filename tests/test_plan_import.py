"""
Plan import endpoint (Phase 4 Step 2). The extraction LLM is mocked; the parse,
intent-gate, task-routing, and persistence paths are exercised for real.
"""
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import storage
from agents import plan_import_agent
from main import app
from models.plan_import import CandidateTask, DocKind, ExtractedPlan, ProjectMeta

client = TestClient(app)

_LONG_TEXT = "Week 1: read chapter one. Week 2: finish the problem set on time."


def _mock_extract(monkeypatch, plan: ExtractedPlan):
    monkeypatch.setattr(plan_import_agent, "extract_plan",
                        AsyncMock(return_value=plan))


def _new_project():
    return client.post("/projects", json={"name": "Course"}).json()["id"]


def test_import_accepted_creates_tasks(clean_stores, monkeypatch):
    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=True, doc_kind=DocKind.syllabus, confidence=0.9,
        project_meta=ProjectMeta(title="ML Course", deadline=date(2026, 12, 1)),
        candidate_tasks=[
            CandidateTask(title="Read chapter one", estimated_hours=2,
                          explicit_date=date(2026, 8, 1)),
            CandidateTask(title="Problem set 1", estimated_hours=3),
        ],
    ))
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import", data={"text": _LONG_TEXT})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True and body["doc_kind"] == "syllabus"
    assert [t["title"] for t in body["tasks"]] == ["Read chapter one", "Problem set 1"]

    # persisted + attributed to the project
    created = [t for t in storage.task_store.values() if t.project_id == pid]
    assert len(created) == 2
    assert all(t.source == "import" for t in created)
    assert set(storage.project_store[pid].task_ids) == {t.id for t in created}
    # explicit_date flowed into the task deadline
    read_ch = next(t for t in created if t.title == "Read chapter one")
    assert read_ch.deadline == date(2026, 8, 1)


def test_import_dry_run_does_not_persist(clean_stores, monkeypatch):
    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=True, confidence=0.8,
        candidate_tasks=[CandidateTask(title="Draft outline")],
    ))
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import",
                    data={"text": _LONG_TEXT, "dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and len(r.json()["tasks"]) == 1
    assert not storage.task_store                       # nothing persisted
    assert storage.project_store[pid].task_ids == []


def test_import_rejects_non_plan(clean_stores, monkeypatch):
    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=False, doc_kind=DocKind.other, confidence=0.1,
        rejection_reason="This looks like an invoice, not a plan.",
    ))
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import", data={"text": _LONG_TEXT})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"].startswith("This looks like an invoice")
    assert not storage.task_store


def test_import_low_confidence_rejected(clean_stores, monkeypatch):
    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=True, confidence=0.4,
        candidate_tasks=[CandidateTask(title="Maybe a task")],
    ))
    pid = _new_project()
    r = client.post(f"/projects/{pid}/import", data={"text": _LONG_TEXT})
    assert r.status_code == 422 and r.json()["detail"]["accepted"] is False


def test_import_too_short_text_422_before_llm(clean_stores, monkeypatch):
    # parse_text runs before extraction; the mock must never be consulted.
    called = AsyncMock(side_effect=AssertionError("LLM should not be called"))
    monkeypatch.setattr(plan_import_agent, "extract_plan", called)
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import", data={"text": "hi"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "too_short"


def test_import_requires_exactly_one_input(clean_stores):
    pid = _new_project()
    # neither file nor text
    assert client.post(f"/projects/{pid}/import", data={}).status_code == 422


def test_import_unknown_project_404(clean_stores):
    assert client.post("/projects/missing/import",
                       data={"text": _LONG_TEXT}).status_code == 404
