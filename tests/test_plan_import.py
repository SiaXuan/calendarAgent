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

    # persisted to the project-scoped store (NOT the global scheduling store)
    assert not storage.task_store
    created = [t for t in storage.project_task_store.values() if t.project_id == pid]
    assert len(created) == 2
    assert all(t.source == "import" for t in created)
    assert set(storage.project_store[pid].task_ids) == {t.id for t in created}
    # explicit_date flowed into the task deadline
    read_ch = next(t for t in created if t.title == "Read chapter one")
    assert read_ch.deadline == date(2026, 8, 1)


def test_import_writes_snapshot_for_review(clean_stores, monkeypatch):
    """A confirmed import writes the plan snapshot straight from the coarse task
    nodes (so "计划节点" fills for review). Reminders are written separately via
    replan, NOT by import."""
    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=True, doc_kind=DocKind.syllabus, confidence=0.9,
        candidate_tasks=[
            CandidateTask(title="Read chapter one", estimated_hours=2,
                          explicit_date=date(2026, 8, 1),
                          source_excerpt="Week 1: read chapter one"),
            CandidateTask(title="Problem set 1", estimated_hours=3),
        ],
    ))
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import", data={"text": _LONG_TEXT})
    assert r.status_code == 200
    body = r.json()
    assert "reminders" not in body        # import no longer writes reminders

    # snapshot now populated → "计划节点" fills immediately for review
    plan = client.get(f"/projects/{pid}/plan").json()
    assert {i["title"] for i in plan["items"]} == {"Read chapter one", "Problem set 1"}

    # source_excerpt persisted onto the task (grounds later daily decomposition)
    read_ch = next(t for t in storage.project_task_store.values()
                   if t.title == "Read chapter one")
    assert read_ch.source_excerpt == "Week 1: read chapter one"


def test_import_image_uses_vision(clean_stores, monkeypatch):
    """A pasted image is routed to Claude vision (extract_plan_from_image), not
    the text parser."""
    monkeypatch.setattr(plan_import_agent, "extract_plan_from_image", AsyncMock(
        return_value=ExtractedPlan(
            is_plan=True, doc_kind=DocKind.schedule_table, confidence=0.9,
            candidate_tasks=[CandidateTask(title="From screenshot", estimated_hours=1)],
        )))
    # If the image path is wrong this would hit the text extractor → make it blow up.
    monkeypatch.setattr(plan_import_agent, "extract_plan",
                        AsyncMock(side_effect=AssertionError("text path used for an image")))
    pid = _new_project()

    r = client.post(f"/projects/{pid}/import",
                    files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n fake", "image/png")})
    assert r.status_code == 200
    assert [t["title"] for t in r.json()["tasks"]] == ["From screenshot"]
    assert any(t.title == "From screenshot" for t in storage.project_task_store.values())


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
        is_plan=True, confidence=0.2,
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


def test_import_applies_date_adjustment(clean_stores, monkeypatch):
    # A 2025 syllabus item, week 2, shifted to the 2027 term via the instruction.
    from agents import plan_import_agent
    from models.plan_import import ImportAdjustment

    _mock_extract(monkeypatch, ExtractedPlan(
        is_plan=True, confidence=0.9,
        candidate_tasks=[CandidateTask(title="Homework 1",
                                       explicit_date=date(2025, 9, 8), week_index=2)],
        adjustment=ImportAdjustment(term_start_date=date(2027, 9, 6), due_weekday=0),
    ))
    pid = _new_project()
    r = client.post(f"/projects/{pid}/import",
                    data={"text": _LONG_TEXT, "instruction": "move to 2027 term, due Mondays"})
    assert r.status_code == 200
    created = [t for t in storage.project_task_store.values() if t.project_id == pid][0]
    # week-2 Monday of the 2027 term (week-1 Monday + 7 days), not the 2025 date
    assert created.deadline.year == 2027 and created.deadline.weekday() == 0


def test_extracted_plan_coerces_stringified_json():
    # Claude sometimes returns candidate_tasks / project_meta as a JSON *string*
    # instead of a native value; the model must decode it, not blow up (was a 500).
    plan = ExtractedPlan.model_validate({
        "is_plan": True,
        "confidence": 0.8,
        "project_meta": '{"title": "ML Course"}',
        "candidate_tasks": '[{"title": "Read chapter one"}, {"title": "Problem set"}]',
    })
    assert plan.project_meta.title == "ML Course"
    assert [c.title for c in plan.candidate_tasks] == ["Read chapter one", "Problem set"]


# ─── re-import reconcile (Step 2 second half) ────────────────────────────────

def _plan(*candidates):
    return ExtractedPlan(
        is_plan=True, doc_kind=DocKind.syllabus, confidence=0.9,
        project_meta=ProjectMeta(title="ML Course"),
        candidate_tasks=list(candidates),
    )


def _import(monkeypatch, pid, plan, **data):
    _mock_extract(monkeypatch, plan)
    return client.post(f"/projects/{pid}/import", data={"text": _LONG_TEXT, **data})


def _mark_done(pid, task, title):
    """Seed a done record directly — these tests are about reconcile, not about
    the completion endpoint (which also needs a generated schedule for the day)."""
    from models.project import CompletionRecord, CompletionStatus
    key = f"{task.id}::{title}"
    storage.completion_store[key] = CompletionRecord(
        block_key=key, project_id=pid, task_id=task.id, title=title,
        status=CompletionStatus.done,
    )
    return key


def test_reimport_revises_instead_of_duplicating(clean_stores, monkeypatch):
    """
    The bug this feature exists for: a second import used to mint fresh uuids and
    append, leaving the project with two of every node.
    """
    pid = _new_project()
    _import(monkeypatch, pid, _plan(
        CandidateTask(title="Read chapter one", estimated_hours=2),
        CandidateTask(title="Problem set 1", estimated_hours=3),
    ))
    first = {t.title: t.id for t in storage.project_task_store.values() if t.project_id == pid}

    # Revised doc: one node unchanged, one dropped, one new.
    r = _import(monkeypatch, pid, _plan(
        CandidateTask(title="Read chapter one", estimated_hours=4),
        CandidateTask(title="Problem set 2", estimated_hours=3),
    ))
    assert r.status_code == 200

    tasks = [t for t in storage.project_task_store.values() if t.project_id == pid]
    assert sorted(t.title for t in tasks) == ["Problem set 2", "Read chapter one"]
    # The surviving node kept its id — completion / reminders / chunks hang off it.
    assert next(t for t in tasks if t.title == "Read chapter one").id == first["Read chapter one"]
    # ...and picked up the revision's content.
    assert next(t for t in tasks if t.title == "Read chapter one").estimated_hours == 4
    assert set(storage.project_store[pid].task_ids) == {t.id for t in tasks}

    diff = r.json()["reconcile"]
    assert [m["new_title"] for m in diff["matched"]] == ["Read chapter one"]
    assert diff["added"] == ["Problem set 2"]
    assert [x["title"] for x in diff["removed"]] == ["Problem set 1"]


def test_reimport_matches_through_cosmetic_title_edits(clean_stores, monkeypatch):
    """Renumbering and punctuation churn shouldn't read as a different node."""
    pid = _new_project()
    _import(monkeypatch, pid, _plan(CandidateTask(title="3. Read chapter one")))
    original = next(t for t in storage.project_task_store.values() if t.project_id == pid)

    r = _import(monkeypatch, pid, _plan(CandidateTask(title="Week 4 — Read Chapter One!")))

    tasks = [t for t in storage.project_task_store.values() if t.project_id == pid]
    assert len(tasks) == 1
    assert tasks[0].id == original.id           # same node
    assert tasks[0].title == "Week 4 — Read Chapter One!"   # new wording wins
    assert r.json()["reconcile"]["matched"][0]["renamed"] is True


def test_reimport_migrates_completion_record_on_rename(clean_stores, monkeypatch):
    """
    block_key is "{task_id}::{title}", so a rename moves the key. Without a
    migration the done flag would be stranded under the old string.
    """
    pid = _new_project()
    _import(monkeypatch, pid, _plan(CandidateTask(title="Read chapter one")))
    task = next(t for t in storage.project_task_store.values() if t.project_id == pid)
    old_key = _mark_done(pid, task, "Read chapter one")

    _import(monkeypatch, pid, _plan(CandidateTask(title="1. Read Chapter One!")))

    new_key = f"{task.id}::1. Read Chapter One!"
    assert old_key not in storage.completion_store
    assert storage.completion_store[new_key].status == "done"
    assert storage.completion_store[new_key].title == "1. Read Chapter One!"


def test_reimport_keeps_done_node_dropped_by_revision(clean_stores, monkeypatch):
    """Finishing something and then revising the syllabus doesn't un-finish it."""
    pid = _new_project()
    _import(monkeypatch, pid, _plan(
        CandidateTask(title="Read chapter one"),
        CandidateTask(title="Problem set 1"),
    ))
    done_task = next(t for t in storage.project_task_store.values()
                     if t.project_id == pid and t.title == "Problem set 1")
    _mark_done(pid, done_task, "Problem set 1")

    # Revised doc no longer lists the finished node.
    r = _import(monkeypatch, pid, _plan(CandidateTask(title="Read chapter one")))

    titles = {t.title for t in storage.project_task_store.values() if t.project_id == pid}
    assert titles == {"Read chapter one", "Problem set 1"}   # done one survives
    assert [k["title"] for k in r.json()["reconcile"]["kept_done"]] == ["Problem set 1"]
    assert r.json()["reconcile"]["removed"] == []


def test_first_import_reports_no_reconcile(clean_stores, monkeypatch):
    """Nothing to reconcile against — the key should be absent, not an empty diff."""
    pid = _new_project()
    r = _import(monkeypatch, pid, _plan(CandidateTask(title="Read chapter one")))
    assert "reconcile" not in r.json()


def test_reimport_dry_run_previews_without_deleting(clean_stores, monkeypatch):
    """Re-import drops nodes, so the preview has to be inspectable first."""
    pid = _new_project()
    _import(monkeypatch, pid, _plan(
        CandidateTask(title="Read chapter one"),
        CandidateTask(title="Problem set 1"),
    ))
    before = {t.id for t in storage.project_task_store.values() if t.project_id == pid}

    r = _import(monkeypatch, pid, _plan(CandidateTask(title="Read chapter one")),
                dry_run="true")

    assert [x["title"] for x in r.json()["reconcile"]["removed"]] == ["Problem set 1"]
    after = {t.id for t in storage.project_task_store.values() if t.project_id == pid}
    assert after == before   # nothing actually removed
