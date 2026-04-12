from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import orchestrator
from agents.chat_agent import handle_message
from api.preferences import get_current_prefs
from models.schedule import DaySchedule

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    date: str   # YYYY-MM-DD


@router.post("/chat", response_model=DaySchedule)
async def chat(payload: ChatRequest):
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    current = orchestrator.schedule_store.get(d)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule for {payload.date}. Generate one first via POST /schedule/generate.",
        )

    params = await handle_message(payload.message, current, get_current_prefs().language)
    updated = await orchestrator.apply_adjustment(d, params)
    return updated
