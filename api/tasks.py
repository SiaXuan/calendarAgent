import asyncio
import json
import logging
import os
import uuid
from datetime import date as date_type

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import orchestrator
from agents.orchestrator import save_task_store
from integrations.caldav_client import fetch_reminders, is_system_list
from models.task import CognitiveLoad, Priority, Task

router = APIRouter()
_log = logging.getLogger("dayflow")

# ── Instant detection ────────────────────────────────────────────────────────
_INSTANT_TRIGGERS = [
    # English
    'pay ', 'send ', 'email ', 'call ', 'text ', 'reply ', 'submit ', 'sign ',
    'buy ', 'order ', 'book ', 'reserve ', 'print ', 'upload ', 'confirm ',
    'transfer ', 'wire ', 'renew ', 'check in',
    # Chinese
    '交', '付款', '还款', '支付', '发送', '提交', '预约', '购买', '订购',
    '签名', '打印', '转账', '汇款', '续费', '确认', '取件', '缴费', '转房租', '交租',
]

# If ANY of these appear in the title, never treat as instant — it's a real task.
# Catches "提交 CV Assessment", "submit homework", "完成 CS project", etc.
_INSTANT_EXCLUSIONS = [
    # English academic / work
    'assignment', 'assessment', 'homework', 'project', 'report',
    'exam', 'quiz', 'midterm', 'final', 'paper', 'essay', 'test',
    'proposal', 'presentation', 'lab', 'problem set', 'ps ',
    # Chinese academic
    '作业', '作文', '测验', '报告', '项目', '考试', '实验', '论文',
    '期末', '期中', '大作业', '小作业', '课程', '完成',
]

# ── High-confidence keyword rules (no LLM needed) ────────────────────────────
# Rules here must hold regardless of surrounding context.
# "problem set" alone is NOT here — "grading problem set" ≠ deep.
# When in doubt, let LLM decide.
_CONFIDENT_DEEP = [
    'implement', 'algorithm', 'debug', 'refactor',   # pure coding/engineering
    'essay', 'thesis',                                # original writing
    'midterm', 'final exam',                          # exams (not just "quiz")
    '算法', '调试', '论文', '期末',
]
_CONFIDENT_LIGHT = [
    'watch lecture', 'watch video', 'watch recorded',
    'schedule meeting', 'book appointment',
    '看视频', '看录播',
]


def _detect_instant(title: str) -> bool:
    tl = title.lower()
    # Never instant if the title looks like an academic/work deliverable
    if any(ex in tl for ex in _INSTANT_EXCLUSIONS):
        return False
    return any(kw in tl for kw in _INSTANT_TRIGGERS)


def _keyword_classify(title: str, description: str | None) -> CognitiveLoad | None:
    """
    High-confidence keyword pass.
    Returns a CognitiveLoad if confident, or None to signal 'needs LLM'.
    """
    text = (title + ' ' + (description or '')).lower()
    if any(kw in text for kw in _CONFIDENT_DEEP):
        return CognitiveLoad.deep
    if any(kw in text for kw in _CONFIDENT_LIGHT):
        return CognitiveLoad.light
    return None   # uncertain — let LLM decide


# ── LLM batch classifier (Haiku, cheap + fast) ───────────────────────────────

async def _llm_classify_batch(
    items: list[dict],   # list of {"id": str, "title": str, "description": str|None}
) -> dict[str, CognitiveLoad]:
    """
    Ask Claude Haiku to classify cognitive load for a batch of tasks.
    Returns {task_id: CognitiveLoad}. Falls back to 'medium' on any error.
    """
    if not items:
        return {}

    lines = "\n".join(
        f"{i+1}. {it['title']}" + (f" — {it['description']}" if it["description"] else "")
        for i, it in enumerate(items)
    )

    system = (
        "Classify the cognitive load of each task. "
        "Reply ONLY with a JSON array of strings, one per task, in order.\n"
        "Use exactly: \"deep\", \"medium\", or \"light\".\n\n"
        "deep  = sustained original thinking required\n"
        "  examples: coding, implementing algorithms, CS/ML/CV/math assignments, "
        "technical tests or assessments (e.g. job test, coding challenge), "
        "writing a paper/essay from scratch, research, debugging, system design\n\n"
        "medium = moderate attention, some judgment but less creative effort\n"
        "  examples: grading complex work (essays, projects, problem sets), "
        "reading a paper with notes, preparing slides, planning, reviewing a PR\n\n"
        "light = low mental effort, mostly mechanical or routine\n"
        "  examples: grading a short quiz or multiple-choice, admin tasks, "
        "scheduling, filing, watching a lecture video, simple data entry\n\n"
        "Key distinctions:\n"
        "- 'grade quiz' or 'grade multiple choice' → light (mechanical checking)\n"
        "- 'grade problem set / essay / project' → medium (requires judgment)\n"
        "- 'test' in a job/interview context (e.g. 'huawei test', 'coding test') → deep\n"
        "- 'due' after a course name (e.g. 'ML due', 'CV due') → deep (it's an assignment)\n"
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": f"Tasks:\n{lines}"}],
        )
        labels: list[str] = json.loads(resp.content[0].text.strip())
        return {
            it["id"]: CognitiveLoad(labels[i]) if i < len(labels) and labels[i] in ("deep", "medium", "light") else CognitiveLoad.medium
            for i, it in enumerate(items)
        }
    except Exception as exc:
        _log.warning("LLM cognitive-load classification failed: %s", exc)
        return {it["id"]: CognitiveLoad.medium for it in items}


class TaskInput(BaseModel):
    title: str
    description: str | None = None
    priority: Priority
    cognitive_load: CognitiveLoad
    estimated_hours: float
    deadline: str | None = None   # YYYY-MM-DD


@router.post("/tasks", response_model=Task)
async def create_task(payload: TaskInput):
    deadline = None
    if payload.deadline:
        try:
            deadline = date_type.fromisoformat(payload.deadline)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid deadline format. Use YYYY-MM-DD.")

    task = Task(
        id=str(uuid.uuid4()),
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        cognitive_load=payload.cognitive_load,
        estimated_hours=payload.estimated_hours,
        deadline=deadline,
        source="manual",
    )
    orchestrator.task_store[task.id] = task
    save_task_store()
    return task


@router.get("/tasks", response_model=list[Task])
async def list_tasks():
    return list(orchestrator.task_store.values())


async def do_sync_reminders() -> dict:
    """
    Core sync logic — shared by the API endpoint and the startup hook.
    Pulls incomplete reminders from iCloud and upserts them into task_store.

    Cognitive-load classification (two-pass):
      Pass 1 — keyword rules: fast, handles unambiguous cases.
      Pass 2 — LLM (Haiku): batch-classifies tasks where keywords gave no signal.
    """
    raw = await asyncio.to_thread(fetch_reminders)
    if not raw:
        return {"added": 0, "updated": 0, "skipped": 0, "tasks": []}

    # Full replace: remove all stale reminder-sourced tasks before upserting.
    # This ensures deleted/completed reminders disappear and stale instant
    # classifications (from previous code versions) don't persist.
    stale_ids = [tid for tid, t in orchestrator.task_store.items() if t.source == "reminders"]
    for tid in stale_ids:
        del orchestrator.task_store[tid]

    added, updated, skipped = 0, 0, 0
    tasks_out = []
    llm_pending: list[dict] = []   # tasks needing LLM classification

    for r in raw:
        source_list = r.get("source_list", "")
        if source_list and is_system_list(source_list):
            skipped += 1
            continue

        task_id = f"reminder_{r['id']}" if r["id"] else f"reminder_{uuid.uuid4()}"
        is_instant = _detect_instant(r["title"])

        # Pass 1: keyword classification
        if is_instant:
            load: CognitiveLoad = CognitiveLoad.light
        else:
            kw_load = _keyword_classify(r["title"], r["description"])
            if kw_load is not None:
                load = kw_load
            else:
                load = CognitiveLoad.medium   # placeholder; replaced after LLM pass
                llm_pending.append({"id": task_id, "title": r["title"], "description": r["description"]})

        task = Task(
            id=task_id,
            title=r["title"],
            description=r["description"],
            priority=Priority(r["priority"]),
            cognitive_load=load,
            estimated_hours=0.08 if is_instant else 0.5,
            deadline=r["deadline"],
            deadline_dt=r.get("deadline_dt"),
            source="reminders",
            is_instant=is_instant,
        )

        if task_id in orchestrator.task_store:
            updated += 1
        else:
            added += 1

        orchestrator.task_store[task_id] = task
        tasks_out.append(task)

    # Pass 2: LLM batch for uncertain tasks
    if llm_pending:
        llm_results = await _llm_classify_batch(llm_pending)
        for task_id, llm_load in llm_results.items():
            if task_id in orchestrator.task_store:
                orig = orchestrator.task_store[task_id]
                orchestrator.task_store[task_id] = orig.model_copy(
                    update={"cognitive_load": llm_load}
                )
        # Reflect LLM loads in tasks_out
        for t in tasks_out:
            if t.id in llm_results:
                t = t.model_copy(update={"cognitive_load": llm_results[t.id]})
        _log.info("LLM classified %d tasks: %s", len(llm_results),
                  {it["title"]: llm_results[it["id"]].value for it in llm_pending})

    save_task_store()
    return {"added": added, "updated": updated, "skipped": skipped, "tasks": tasks_out}


@router.post("/tasks/sync/reminders")
async def sync_reminders():
    """Pull incomplete reminders from iCloud and upsert into task_store."""
    return await do_sync_reminders()


@router.post("/tasks/reclassify")
async def reclassify_tasks():
    """
    Re-run hybrid cognitive-load classification (keyword + LLM) on all tasks
    currently in task_store. Useful after updating classification rules.
    """
    tasks = list(orchestrator.task_store.values())
    if not tasks:
        return {"reclassified": 0, "results": []}

    llm_pending: list[dict] = []
    keyword_updates: dict[str, CognitiveLoad] = {}

    for task in tasks:
        if task.is_instant:
            keyword_updates[task.id] = CognitiveLoad.light
        else:
            kw_load = _keyword_classify(task.title, task.description)
            if kw_load is not None:
                keyword_updates[task.id] = kw_load
            else:
                llm_pending.append({"id": task.id, "title": task.title, "description": task.description})

    # Apply keyword results immediately
    for task_id, load in keyword_updates.items():
        if task_id in orchestrator.task_store:
            orchestrator.task_store[task_id] = orchestrator.task_store[task_id].model_copy(
                update={"cognitive_load": load}
            )

    # Batch LLM pass for the rest
    llm_results: dict[str, CognitiveLoad] = {}
    if llm_pending:
        llm_results = await _llm_classify_batch(llm_pending)
        for task_id, load in llm_results.items():
            if task_id in orchestrator.task_store:
                orchestrator.task_store[task_id] = orchestrator.task_store[task_id].model_copy(
                    update={"cognitive_load": load}
                )
        _log.info("Reclassify: LLM classified %d tasks: %s", len(llm_results),
                  {it["title"]: llm_results[it["id"]].value for it in llm_pending})

    all_updates = {**keyword_updates, **llm_results}
    results = [
        {"id": tid, "title": orchestrator.task_store[tid].title, "cognitive_load": load.value}
        for tid, load in all_updates.items()
        if tid in orchestrator.task_store
    ]
    return {"reclassified": len(all_updates), "results": results}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id not in orchestrator.task_store:
        raise HTTPException(status_code=404, detail="Task not found.")
    del orchestrator.task_store[task_id]
    save_task_store()
    return {"deleted": task_id}
