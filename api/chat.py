from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from graphs.adjust_graph import run_adjust_graph
from models.schedule import DaySchedule
from storage import schedule_store

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

    if schedule_store.get(d) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule for {payload.date}. Generate one first via POST /schedule/generate.",
        )

    updated, _params = await run_adjust_graph(d, payload.message)
    return updated
