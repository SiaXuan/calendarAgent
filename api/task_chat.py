"""
Task Chat API — per-task planning conversations with the LLM.
POST /chat/task/{task_id}         → send a message, get a reply (+ optional plan)
POST /chat/task/{task_id}/confirm → commit a decomposed plan to the schedule
"""
from datetime import date as date_type

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from agents import orchestrator
from agents.task_chat_agent import ChatMessage, TaskChatResult, chat
from api.preferences import get_current_prefs
from models.task import Subtask

router = APIRouter()


class TaskChatRequest(BaseModel):
    messages: list[ChatMessage]
    target_date: str | None = None   # YYYY-MM-DD, defaults to today


class ConfirmPlanRequest(BaseModel):
    subtasks: list[dict]


@router.post("/chat/task/{task_id}", response_model=TaskChatResult)
async def task_chat(task_id: str, payload: TaskChatRequest):
    """Send a message in a per-task planning chat. Returns AI reply + optional plan."""
    task = orchestrator.task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    target_date = date_type.today()
    if payload.target_date:
        try:
            target_date = date_type.fromisoformat(payload.target_date)
        except ValueError:
            pass

    language = get_current_prefs().language
    return await chat(task, payload.messages, target_date, language)


@router.post("/chat/task/{task_id}/confirm")
async def confirm_task_plan(task_id: str, payload: ConfirmPlanRequest):
    """
    Commit a confirmed decomposition plan.
    On the next schedule generation, these subtasks replace Claude's decomposition for this task.
    """
    if task_id not in orchestrator.task_store:
        raise HTTPException(status_code=404, detail="Task not found.")

    try:
        subtasks = [Subtask.model_validate(s) for s in payload.subtasks]
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    orchestrator.subtask_overrides[task_id] = subtasks

    # Invalidate cached schedule so next generate picks up the new plan
    orchestrator.schedule_store.clear()

    return {"confirmed": len(subtasks), "task_id": task_id}
