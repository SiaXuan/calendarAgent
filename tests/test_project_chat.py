"""
Per-project planning conversation (Phase 4). The LLM is mocked; the memory
(history persists), plan revision (task list replaced, ids preserved by title),
and endpoint wiring are exercised for real.
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import storage
from agents import project_chat
from agents.project_chat import PlanChatResult
from main import app
from models.plan_import import CandidateTask
from models.task import CognitiveLoad, Priority, Task

client = TestClient(app)


def _project_with_task():
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    storage.project_task_store["t1"] = Task(
        id="t1", title="作业1", priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=4, project_id=pid)
    return pid


def test_chat_reply_without_change_keeps_plan(clean_stores, monkeypatch):
    pid = _project_with_task()
    monkeypatch.setattr(project_chat, "converse",
                        AsyncMock(return_value=PlanChatResult(reply="作业1 大概 4 小时。", tasks=None)))

    r = client.post(f"/projects/{pid}/chat", data={"message": "作业1 要多久？"})
    assert r.status_code == 200
    assert r.json() == {"reply": "作业1 大概 4 小时。", "plan_changed": False,
                        "progress_applied": 0}

    # both turns persisted as the project's memory
    msgs = client.get(f"/projects/{pid}/chat").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    # plan untouched
    assert {t.title for t in storage.project_task_store.values()} == {"作业1"}


def test_chat_with_attachment_never_hard_rejects(clean_stores, monkeypatch):
    """An attached doc goes through the conversation — no intent gate. Even when
    the model doesn't turn it into tasks, it replies (fallback) instead of 422."""
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    captured = {}

    async def fake(name, plan, history, message, language, doc_text=None, image=None):
        captured["doc_text"] = doc_text
        return PlanChatResult(reply="这看起来像一份目录，想按它排计划吗？", tasks=None)
    monkeypatch.setattr(project_chat, "converse", fake)

    toc = "第一章 简介\n第二章 方法\n第三章 结果\n第四章 讨论\n".encode()
    r = client.post(f"/projects/{pid}/chat",
                    data={"message": "看看这个"},
                    files={"file": ("toc.txt", toc, "text/plain")})
    assert r.status_code == 200
    assert r.json()["plan_changed"] is False
    assert "目录" in r.json()["reply"]
    assert captured["doc_text"] and "第一章" in captured["doc_text"]   # doc reached the LLM


def test_chat_revises_plan_and_preserves_ids(clean_stores, monkeypatch):
    pid = _project_with_task()
    # split 作业1 (kept, same title → same id) + add a new task
    monkeypatch.setattr(project_chat, "converse", AsyncMock(return_value=PlanChatResult(
        reply="好的，拆成两部分。",
        tasks=[
            CandidateTask(title="作业1", estimated_hours=2),
            CandidateTask(title="作业1 复盘", estimated_hours=1),
        ])))

    r = client.post(f"/projects/{pid}/chat", data={"message": "作业1 太重，拆一下"})
    assert r.status_code == 200 and r.json()["plan_changed"] is True

    tasks = {t.title: t for t in storage.project_task_store.values()}
    assert set(tasks) == {"作业1", "作业1 复盘"}
    assert tasks["作业1"].id == "t1"                 # id preserved for unchanged title
    assert tasks["作业1"].estimated_hours == 2       # fields updated
    # snapshot rebuilt → 计划节点 reflects the revision
    plan = client.get(f"/projects/{pid}/plan").json()
    assert {i["title"] for i in plan["items"]} == {"作业1", "作业1 复盘"}
