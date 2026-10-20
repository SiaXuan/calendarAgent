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


# ─── dates are computed in code, not written by the model ──────────────────

def _project_with_dated_tasks():
    from datetime import date
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    storage.project_task_store["t1"] = Task(
        id="t1", title="作业3", priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=4, project_id=pid,
        deadline=date(2026, 10, 24))
    storage.project_task_store["t2"] = Task(
        id="t2", title="期中复习", priority=Priority.medium,
        cognitive_load=CognitiveLoad.deep, estimated_hours=6, project_id=pid,
        deadline=date(2026, 11, 2))
    return pid


def _say(monkeypatch, reply, tasks):
    monkeypatch.setattr(project_chat, "converse", AsyncMock(
        return_value=PlanChatResult(reply=reply, tasks=tasks)))


def test_relative_shift_is_computed_from_the_current_deadline(clean_stores, monkeypatch):
    """"推迟两天" arrives as shift_days=2; the date is worked out from the
    task's stored deadline, not by the model."""
    from models.plan_import import DeadlineChange
    from datetime import date
    pid = _project_with_dated_tasks()
    _say(monkeypatch, "好，作业3推迟到[[deadline:作业3]]。", [
        CandidateTask(title="作业3", deadline_change=DeadlineChange(shift_days=2)),
        CandidateTask(title="期中复习"),
    ])
    r = client.post(f"/projects/{pid}/chat", data={"message": "作业3推迟两天"})

    assert storage.project_task_store["t1"].deadline == date(2026, 10, 26)
    assert "10月26日" in r.json()["reply"]
    assert "[[" not in r.json()["reply"]


def test_unchanged_task_keeps_its_date_when_the_model_omits_it(clean_stores, monkeypatch):
    """The model returns the complete task list but no longer copies dates for
    tasks it isn't changing — an omitted date means "keep", not "clear"."""
    from datetime import date
    pid = _project_with_dated_tasks()
    _say(monkeypatch, "改好了。", [
        CandidateTask(title="作业3"),
        CandidateTask(title="期中复习"),
    ])
    client.post(f"/projects/{pid}/chat", data={"message": "把作业3拆细一点"})

    assert storage.project_task_store["t1"].deadline == date(2026, 10, 24)
    assert storage.project_task_store["t2"].deadline == date(2026, 11, 2)


def test_full_date_quoted_by_the_user_is_still_honoured(clean_stores, monkeypatch):
    from datetime import date
    pid = _project_with_dated_tasks()
    _say(monkeypatch, "改到[[deadline:作业3]]。", [
        CandidateTask(title="作业3", explicit_deadline=date(2026, 12, 1)),
        CandidateTask(title="期中复习"),
    ])
    r = client.post(f"/projects/{pid}/chat", data={"message": "作业3改到2026-12-01"})
    assert storage.project_task_store["t1"].deadline == date(2026, 12, 1)
    assert "12月1日" in r.json()["reply"]


def test_stored_history_carries_the_filled_dates(clean_stores, monkeypatch):
    """The history is fed back to the model next turn, so it must hold real
    dates, not placeholders."""
    from models.plan_import import DeadlineChange
    pid = _project_with_dated_tasks()
    _say(monkeypatch, "推迟到[[deadline:作业3]]。", [
        CandidateTask(title="作业3", deadline_change=DeadlineChange(shift_days=2)),
        CandidateTask(title="期中复习"),
    ])
    client.post(f"/projects/{pid}/chat", data={"message": "作业3推迟两天"})
    last = storage.project_chat_store[pid][-1]["content"]
    assert "10月26日" in last and "[[" not in last


# ─── resolution and formatting ─────────────────────────────────────────────

def test_resolver_cases():
    from datetime import date
    from agents.plan_reschedule import resolve_deadline_change as resolve
    from models.plan_import import DeadlineChange as C
    today = date(2026, 10, 20)   # a Tuesday
    assert resolve(C(shift_days=-7), date(2026, 10, 24), today) == date(2026, 10, 17)
    assert resolve(C(weekday=4, weeks_from_now=1), None, today) == date(2026, 10, 30)
    assert resolve(C(weekday=4), None, today) == date(2026, 10, 23)
    assert resolve(C(weekday=1), None, today) == today          # "周二" said on a Tuesday
    assert resolve(C(month=11, day=3), None, today) == date(2026, 11, 3)
    assert resolve(C(month=2, day=30), None, today) == date(2027, 2, 28)   # clamped
    assert resolve(None, date(2026, 10, 24), today) is None
    assert resolve(C(shift_days=3), None, today) is None       # nothing to shift from


def test_month_day_picks_the_nearest_year():
    """A syllabus pasted mid-term lists dates already behind us: "9/14" in
    October is last month, not next September."""
    from datetime import date
    from agents.plan_reschedule import resolve_deadline_change as resolve
    from models.plan_import import DeadlineChange as C
    today = date(2026, 10, 20)
    assert resolve(C(month=9, day=14), None, today) == date(2026, 9, 14)
    assert resolve(C(month=1, day=15), None, today) == date(2027, 1, 15)


def test_placeholder_formatting_per_language():
    from datetime import date
    from agents.project_chat import fill_deadline_placeholders as fill
    t = Task(id="x", title="作业3", priority=Priority.medium,
             cognitive_load=CognitiveLoad.deep, estimated_hours=1,
             deadline=date(2026, 10, 26))
    today = date(2026, 10, 20)
    assert fill("[[deadline:作业3]]", [t], Language.zh_CN, today) == "10月26日（周一）"
    assert fill("[[deadline:作业3]]", [t], Language.zh_TW, today) == "10月26日（週一）"
    assert fill("[[deadline:作业3]]", [t], Language.ja, today) == "10月26日（月）"
    assert fill("[[deadline:作业3]]", [t], Language.en, today) == "Mon, Oct 26"
    # another year shows the year
    assert fill("[[deadline:作业3]]", [t], Language.en, date(2025, 1, 1)) == "Mon, Oct 26, 2026"


def test_placeholder_falls_back_rather_than_leaking():
    """An unknown title or a task without a date must never leave raw
    placeholder syntax in front of the user."""
    from agents.project_chat import fill_deadline_placeholders as fill
    t = Task(id="x", title="作业3", priority=Priority.medium,
             cognitive_load=CognitiveLoad.deep, estimated_hours=1)
    assert fill("[[deadline:作业3]]", [t], Language.zh_CN) == "（未定日期）"
    assert fill("[[deadline:不存在]]", [t], Language.zh_CN) == "（日期见计划）"
    # normalised title match: a renumbered heading still resolves
    assert "未定日期" in fill("[[deadline:1. 作业3]]", [t], Language.zh_CN)
