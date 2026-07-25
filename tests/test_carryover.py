"""
Daily dynamic reschedule + carryover (Phase 4, Step 1.6 续).

Two mechanisms, both tested here against the plan-of-record / completion-overlay
design:
- Structural carryover: a project chunk scheduled for a past day that isn't
  checked done rolls into today (`chunk_subtasks_for_date`), marked carried_over,
  with a STABLE block_key so completing it collapses the carry.
- Automatic incremental planning: `ensure_multiday_plan` only runs the LLM for
  nodes newly entered the window; already-planned projects are untouched.
- Conversational progress: `_apply_chat_progress` marks a task's chunks done from
  a chat report so they stop being scheduled/carried.
"""
from datetime import date, timedelta

import pytest

from agents import multiday_planner as mp
from agents import project_service as svc
from models.planning import DayCapacity, PlannedChunk
from models.project import CompletionRecord, CompletionStatus
from models.task import CognitiveLoad, Priority, Task

TODAY = date(2026, 7, 24)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _chunk(task_id="t1", title="读材料", d=TODAY, pid="p1", minutes=60):
    return PlannedChunk(
        project_id=pid, task_id=task_id, task_title="作业1", title=title,
        date=d, minutes=minutes, cognitive_load=CognitiveLoad.deep)


def _mark_done(chunk):
    import storage
    bkey = f"{chunk.task_id}::{chunk.title}"
    storage.completion_store[bkey] = CompletionRecord(
        block_key=bkey, project_id=chunk.project_id, task_id=chunk.task_id,
        title=chunk.title, scheduled_date=chunk.date,
        status=CompletionStatus.done)


# ── Structural carryover ──────────────────────────────────────────────────────

def test_unfinished_past_chunk_carries_to_today(clean_stores):
    import storage
    storage.multiday_plan_store["p1"] = [_chunk(d=YESTERDAY)]
    subs = svc.chunk_subtasks_for_date(TODAY)
    assert len(subs) == 1
    assert subs[0].carried_over is True
    assert subs[0].suggested_date == TODAY
    assert subs[0].title == "读材料"          # title verbatim → stable block_key


def test_done_past_chunk_does_not_carry(clean_stores):
    import storage
    c = _chunk(d=YESTERDAY)
    storage.multiday_plan_store["p1"] = [c]
    _mark_done(c)
    assert svc.chunk_subtasks_for_date(TODAY) == []


def test_todays_chunk_is_not_marked_carried(clean_stores):
    import storage
    storage.multiday_plan_store["p1"] = [_chunk(d=TODAY)]
    subs = svc.chunk_subtasks_for_date(TODAY)
    assert len(subs) == 1 and subs[0].carried_over is False


def test_future_chunk_not_injected_today(clean_stores):
    import storage
    storage.multiday_plan_store["p1"] = [_chunk(d=TOMORROW)]
    assert svc.chunk_subtasks_for_date(TODAY) == []


def test_carried_block_key_matches_original(clean_stores):
    """The carried subtask's block_key equals the original day's — so ticking it
    done stops it carrying forever."""
    import storage
    c = _chunk(d=YESTERDAY)
    storage.multiday_plan_store["p1"] = [c]

    carried = svc.chunk_subtasks_for_date(TODAY)[0]
    assert svc.subtask_block_key(carried) == f"{c.task_id}::{c.title}"

    _mark_done(c)                                   # user ticks the carried block
    assert svc.chunk_subtasks_for_date(TOMORROW) == []   # no longer carries


# ── Automatic incremental planning ───────────────────────────────────────────

def _task(tid, deadline_days, pid="p1"):
    return Task(
        id=tid, title=f"作业{tid}", priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=2.0,
        deadline=TODAY + timedelta(days=deadline_days), project_id=pid)


@pytest.mark.asyncio
async def test_ensure_plan_only_runs_llm_for_new_nodes(clean_stores, monkeypatch):
    import storage
    storage.project_task_store["t1"] = _task("t1", 2)

    calls = {"n": 0}

    async def fake_plan(nodes, caps, today, language):
        calls["n"] += 1
        return mp.greedy_plan(nodes, caps, today)
    monkeypatch.setattr(mp, "plan_project_work", fake_plan)

    r1 = await svc.ensure_multiday_plan(TODAY, {})
    assert r1["planned"] > 0 and calls["n"] == 1

    # Second call, nothing new in-window → planner is NOT invoked again.
    r2 = await svc.ensure_multiday_plan(TODAY, {})
    assert r2["planned"] == 0 and calls["n"] == 1


@pytest.mark.asyncio
async def test_ensure_plan_subtracts_existing_chunks_from_capacity(clean_stores, monkeypatch):
    import storage
    # p1 already fully planned on TODAY (occupies capacity); p2 is the new node.
    storage.project_task_store["t1"] = _task("t1", 2, pid="p1")
    storage.project_task_store["t2"] = _task("t2", 2, pid="p2")
    storage.multiday_plan_store["p1"] = [_chunk(task_id="t1", d=TODAY, pid="p1", minutes=120)]

    seen_caps = {}

    async def fake_plan(nodes, caps, today, language):
        seen_caps[today] = {c.date: c.free_minutes for c in caps}
        return mp.greedy_plan(nodes, caps, today)
    monkeypatch.setattr(mp, "plan_project_work", fake_plan)

    # fixed calendar already books 60 on TODAY; existing chunk adds 120 more.
    await svc.ensure_multiday_plan(TODAY, {TODAY: 60})
    # The new node saw TODAY reduced by 60 (fixed) + 120 (existing chunk) = 180
    # relative to a day with no commitments (TOMORROW), same work window.
    caps = seen_caps[TODAY]
    assert caps[TODAY] == caps[TOMORROW] - 180


@pytest.mark.asyncio
async def test_ensure_plan_prunes_done_and_deleted(clean_stores, monkeypatch):
    import storage
    # A chunk whose task was deleted, and one fully done → both pruned.
    storage.multiday_plan_store["pX"] = [_chunk(task_id="gone", pid="pX")]
    done_c = _chunk(task_id="t9", pid="pY", d=YESTERDAY)
    storage.multiday_plan_store["pY"] = [done_c]
    _mark_done(done_c)

    async def fake_plan(nodes, caps, today, language):
        return []
    monkeypatch.setattr(mp, "plan_project_work", fake_plan)

    await svc.ensure_multiday_plan(TODAY, {})
    assert "pX" not in storage.multiday_plan_store
    assert "pY" not in storage.multiday_plan_store


# ── Conversational progress ──────────────────────────────────────────────────

def test_chat_progress_done_stops_carry_and_counts(clean_stores):
    import storage
    from agents.project_chat import TaskProgress

    storage.project_task_store["t1"] = _task("t1", 2)
    c1 = _chunk(task_id="t1", title="第1步", d=YESTERDAY)
    c2 = _chunk(task_id="t1", title="第2步", d=TODAY)
    storage.multiday_plan_store["p1"] = [c1, c2]

    n = svc._apply_chat_progress("p1", [TaskProgress(task_title="作业t1", status="done")])
    assert n == 2
    assert svc.chunk_subtasks_for_date(TODAY) == []       # nothing left to schedule
    assert svc.completion_heatmap(YESTERDAY, TOMORROW)     # done chunks on the wall


def test_chat_progress_in_progress_clears_done(clean_stores):
    import storage
    from agents.project_chat import TaskProgress

    storage.project_task_store["t1"] = _task("t1", 2)
    c = _chunk(task_id="t1", d=YESTERDAY)
    storage.multiday_plan_store["p1"] = [c]
    _mark_done(c)

    svc._apply_chat_progress("p1", [TaskProgress(task_title="作业t1", status="in_progress")])
    subs = svc.chunk_subtasks_for_date(TODAY)
    assert len(subs) == 1 and subs[0].carried_over is True   # flows again
