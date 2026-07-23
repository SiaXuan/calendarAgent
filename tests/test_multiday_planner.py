"""
Multi-day planner (Phase 4, Step 1.6). The LLM path is exercised via the greedy
fallback (deterministic) plus a mocked-LLM end-to-end through the endpoint. What
matters: capacity/deadline invariants, and that only in-window deadlines are
planned (far-deadline nodes stay off the radar).
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from agents import multiday_planner as mp
from agents.multiday_planner import build_capacities, greedy_plan
from main import app
from models.planning import DayCapacity
from models.task import CognitiveLoad, Priority, Task

client = TestClient(app)
TODAY = date(2026, 7, 23)


def _task(tid, hours, deadline_days, pid="p1"):
    return Task(
        id=tid, title=f"作业{tid}", priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=hours,
        deadline=TODAY + timedelta(days=deadline_days), project_id=pid,
    )


def test_build_capacities_subtracts_fixed():
    caps = build_capacities(TODAY, TODAY + timedelta(days=2), 480,
                            {TODAY + timedelta(days=1): 180})
    assert [c.free_minutes for c in caps] == [480, 300, 480]


def test_greedy_never_exceeds_capacity_or_deadline():
    nodes = [_task("a", 8, 2), _task("b", 3, 4)]   # 480 + 180 min
    caps = [DayCapacity(date=TODAY + timedelta(days=i), free_minutes=240) for i in range(5)]
    chunks = greedy_plan(nodes, caps, TODAY)

    per_day: dict = {}
    for c in chunks:
        per_day[c.date] = per_day.get(c.date, 0) + c.minutes
    assert all(v <= 240 for v in per_day.values())          # capacity respected
    for tid, dl_days in [("a", 2), ("b", 4)]:
        ds = [c.date for c in chunks if c.task_id == tid]
        assert ds and max(ds) <= TODAY + timedelta(days=dl_days)
    # each chunk carries its parent task + a (distinct) step title
    assert all(c.task_title and c.title for c in chunks)


def test_greedy_splits_big_task_across_days():
    nodes = [_task("a", 6, 5)]   # 360 min, cap 120/day → at least 3 days
    caps = [DayCapacity(date=TODAY + timedelta(days=i), free_minutes=200) for i in range(6)]
    chunks = greedy_plan(nodes, caps, TODAY)
    assert len({c.date for c in chunks}) >= 3
    assert sum(c.minutes for c in chunks) == 360


def test_plan_multiday_only_plans_in_window_deadlines(clean_stores, monkeypatch):
    """A node due within the window is planned; a far-deadline node is left out.
    Deadlines are relative to the real today because the endpoint uses it."""
    import storage
    from agents import project_service as svc

    real_today = date.today()

    def rt(tid, hours, deadline_days):
        return Task(
            id=tid, title=f"作业{tid}", priority=Priority.medium,
            cognitive_load=CognitiveLoad.deep, estimated_hours=hours,
            deadline=real_today + timedelta(days=deadline_days), project_id="p1")

    storage.project_task_store["near"] = rt("near", 4, 3)    # due in 3 days → in
    storage.project_task_store["far"] = rt("far", 4, 40)     # due in 40 days → out

    async def fake_plan(nodes, caps, today, language):
        return mp.greedy_plan(nodes, caps, today)
    monkeypatch.setattr(mp, "plan_project_work", fake_plan)

    r = client.post("/projects/plan-multiday", json={"fixed_minutes_by_date": {}})
    assert r.status_code == 200 and r.json()["chunks"] > 0

    planned_ids = {c.task_id for cs in storage.multiday_plan_store.values() for c in cs}
    assert "near" in planned_ids
    assert "far" not in planned_ids            # far deadline stays off the radar

    # today's schedule picks up the near task's first session, with a real title
    subs_today = svc.chunk_subtasks_for_date(real_today)
    assert subs_today and all(s.project_id == "p1" for s in subs_today)
