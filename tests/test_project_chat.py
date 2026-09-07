"""
Per-project planning conversation (Phase 4). The LLM is mocked; the memory
(history persists), plan revision (task list replaced, ids preserved by title),
and endpoint wiring are exercised for real.
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import storage
from agents import project_chat
from agents.project_chat import PlanChatResult, _SYSTEM_PROMPT
from main import app
from models.plan_import import CandidateTask
from models.task import CognitiveLoad, Priority, Task
from models.user import Language

client = TestClient(app)


def test_system_prompt_formats_without_keyerror():
    """The prompt contains literal braces (e.g. {task_title, status}) that must
    be escaped so .format() only fills name/language. Regression: an unescaped
    brace made converse() raise KeyError → every project-chat turn 502'd. The
    other tests here mock converse away, so this is the only guard on .format()."""
    for lang in Language:
        _SYSTEM_PROMPT.format(name="X", language=lang.value)


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


def test_chat_revision_preserves_id_through_restyled_title(clean_stores, monkeypatch):
    """
    The chat used to match on exact title, so restyling a heading read as
    "old node deleted, new node added" — orphaning its completion record and
    resetting the reminder. Both paths now share plan_reconcile's normalisation.
    """
    pid = _project_with_task()
    from models.project import CompletionRecord, CompletionStatus
    storage.completion_store["t1::作业1"] = CompletionRecord(
        block_key="t1::作业1", project_id=pid, task_id="t1", title="作业1",
        status=CompletionStatus.done)

    monkeypatch.setattr(project_chat, "converse", AsyncMock(return_value=PlanChatResult(
        reply="改个名。", tasks=[CandidateTask(title="1. 作业1", estimated_hours=4)])))
    r = client.post(f"/projects/{pid}/chat", data={"message": "给作业1 加个序号"})
    assert r.status_code == 200 and r.json()["plan_changed"] is True

    tasks = [t for t in storage.project_task_store.values() if t.project_id == pid]
    assert len(tasks) == 1 and tasks[0].id == "t1" and tasks[0].title == "1. 作业1"
    # the done flag followed the rename instead of being stranded
    assert "t1::作业1" not in storage.completion_store
    assert storage.completion_store["t1::1. 作业1"].status == "done"


def test_chat_revision_keeps_finished_task_it_stops_mentioning(clean_stores, monkeypatch):
    """A revision that omits already-done work shouldn't erase the record of it."""
    pid = _project_with_task()
    from models.project import CompletionRecord, CompletionStatus
    storage.completion_store["t1::作业1"] = CompletionRecord(
        block_key="t1::作业1", project_id=pid, task_id="t1", title="作业1",
        status=CompletionStatus.done)

    monkeypatch.setattr(project_chat, "converse", AsyncMock(return_value=PlanChatResult(
        reply="换成新任务。", tasks=[CandidateTask(title="作业2", estimated_hours=2)])))
    client.post(f"/projects/{pid}/chat", data={"message": "改成做作业2"})

    titles = {t.title for t in storage.project_task_store.values() if t.project_id == pid}
    assert titles == {"作业1", "作业2"}
    assert storage.completion_store["t1::作业1"].status == "done"


def test_chat_revision_forgets_completions_of_dropped_pending_task(clean_stores, monkeypatch):
    """A dropped node that was never finished takes its leftovers with it."""
    pid = _project_with_task()
    from models.project import CompletionRecord, CompletionStatus
    storage.completion_store["t1::作业1"] = CompletionRecord(
        block_key="t1::作业1", project_id=pid, task_id="t1", title="作业1",
        status=CompletionStatus.skipped)

    monkeypatch.setattr(project_chat, "converse", AsyncMock(return_value=PlanChatResult(
        reply="换掉。", tasks=[CandidateTask(title="作业2", estimated_hours=2)])))
    client.post(f"/projects/{pid}/chat", data={"message": "改成做作业2"})

    assert "t1" not in storage.project_task_store
    assert "t1::作业1" not in storage.completion_store
