import asyncio
import uuid
from datetime import date as date_type

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import orchestrator
from integrations.caldav_client import fetch_reminders, is_system_list
from models.task import CognitiveLoad, Priority, Task

router = APIRouter()

# Title patterns that indicate a task is a quick action (< 10 min)
_INSTANT_TRIGGERS = [
    # English
    'pay ', 'send ', 'email ', 'call ', 'text ', 'reply ', 'submit ', 'sign ',
    'buy ', 'order ', 'book ', 'reserve ', 'print ', 'upload ', 'confirm ',
    'transfer ', 'wire ', 'renew ', 'check in',
    # Chinese
    '交', '付款', '还款', '支付', '发送', '提交', '预约', '购买', '订购',
    '签名', '打印', '转账', '汇款', '续费', '确认', '取件', '缴费', '转房租', '交租',
]


def _detect_instant(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in _INSTANT_TRIGGERS)


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
    return task


@router.get("/tasks", response_model=list[Task])
async def list_tasks():
    return list(orchestrator.task_store.values())


async def do_sync_reminders() -> dict:
    """
    Core sync logic — shared by the API endpoint and the startup hook.
    Pulls incomplete reminders from iCloud and upserts them into task_store.
    """
    raw = await asyncio.to_thread(fetch_reminders)
    if not raw:
        return {"added": 0, "updated": 0, "skipped": 0, "tasks": []}

    added, updated, skipped = 0, 0, 0
    tasks_out = []

    for r in raw:
        source_list = r.get("source_list", "")
        if source_list and is_system_list(source_list):
            skipped += 1
            continue

        task_id = f"reminder_{r['id']}" if r["id"] else f"reminder_{uuid.uuid4()}"

        is_instant = _detect_instant(r["title"])

        task = Task(
            id=task_id,
            title=r["title"],
            description=r["description"],
            priority=Priority(r["priority"]),
            cognitive_load=CognitiveLoad.light if is_instant else CognitiveLoad.medium,
            estimated_hours=0.08 if is_instant else 0.5,
            deadline=r["deadline"],
            source="reminders",
            is_instant=is_instant,
        )

        if task_id in orchestrator.task_store:
            updated += 1
        else:
            added += 1

        orchestrator.task_store[task_id] = task
        tasks_out.append(task)

    return {"added": added, "updated": updated, "skipped": skipped, "tasks": tasks_out}


@router.post("/tasks/sync/reminders")
async def sync_reminders():
    """Pull incomplete reminders from iCloud and upsert into task_store."""
    return await do_sync_reminders()


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id not in orchestrator.task_store:
        raise HTTPException(status_code=404, detail="Task not found.")
    del orchestrator.task_store[task_id]
    return {"deleted": task_id}
