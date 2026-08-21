"""Decomposition caching (agents/nodes._decompose_with_cache).

The point: a task's subtask TITLES must stay identical across regenerations so
`{task_id}::{title}` block_keys don't drift — otherwise pin/complete/carryover
keys go stale after a re-generate. Only new/edited tasks should hit the LLM.
"""
from datetime import date

from agents import nodes, task_agent
from models.task import CognitiveLoad, Priority, Subtask, Task, TaskKind
from models.user import Language


def _task(tid: str, title: str) -> Task:
    return Task(
        id=tid, title=title, priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=1.0,
    )


async def test_cache_reuses_and_only_re_decomposes_on_change(clean_stores, monkeypatch):
    calls = {"n": 0}

    async def fake_rank(tasks, target_date, language, memory_context=None):
        calls["n"] += 1
        run = calls["n"]  # different wording each call — proves caching prevents drift
        return [
            Subtask(parent_id=t.id, title=f"{t.title} — draft {run}",
                    cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
                    estimated_minutes=60, suggested_date=target_date)
            for t in tasks
        ]

    monkeypatch.setattr(task_agent, "rank_and_decompose", fake_rank)
    t = _task("rem_leet", "LeetCode")
    d = date(2026, 5, 15)

    r1 = await nodes._decompose_with_cache([t], d, Language.en)
    r2 = await nodes._decompose_with_cache([t], d, Language.en)
    assert calls["n"] == 1                    # 2nd regeneration reused cache, no LLM
    assert r1[0].title == r2[0].title         # stable title → stable block_key
    assert r2[0].suggested_date == d          # re-stamped for the day generated

    # editing the task's content → hash changes → re-decompose
    r3 = await nodes._decompose_with_cache(
        [t.model_copy(update={"title": "LeetCode (hard)"})], d, Language.en)
    assert calls["n"] == 2
    assert r3[0].title != r1[0].title


async def test_block_key_identical_across_days(clean_stores, monkeypatch):
    import random

    async def fake_rank(tasks, target_date, language, memory_context=None):
        return [
            Subtask(parent_id=t.id, title=f"{t.title} {random.randint(0, 99999)}",
                    cognitive_load=CognitiveLoad.medium, task_kind=TaskKind.analytical,
                    estimated_minutes=30, suggested_date=target_date)
            for t in tasks
        ]

    monkeypatch.setattr(task_agent, "rank_and_decompose", fake_rank)
    t = _task("rem_x", "X")

    a = await nodes._decompose_with_cache([t], date(2026, 6, 1), Language.en)
    b = await nodes._decompose_with_cache([t], date(2026, 6, 2), Language.en)  # next day
    assert f"{a[0].parent_id}::{a[0].title}" == f"{b[0].parent_id}::{b[0].title}"
