from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from graphs.adjust_graph import run_adjust_graph
from graphs.agent_run import AgentChatResult, confirm_proposal, run_chat_agent
from models.schedule import DaySchedule
from storage import schedule_store

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    date: str   # YYYY-MM-DD


@router.post("/chat", response_model=DaySchedule)
async def chat(payload: ChatRequest):
    """Legacy fixed-schema adjustment (fallback). New UI uses /chat/agent."""
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


# ─── Conversational ReAct agent (S3) ─────────────────────────────────────────

@router.post("/chat/agent", response_model=AgentChatResult)
async def chat_agent_endpoint(payload: ChatRequest):
    """
    Real tool-calling agent. Returns a terminal state:
      success     — committed a minor change (schedule attached)
      proposal    — major change staged, awaiting confirm (proposal attached)
      clarification — agent needs info (message is its question)
      degraded    — couldn't do it / error (schedule unchanged)
      no_change   — nothing needed
    """
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return await run_chat_agent(d, payload.message)


class ConfirmRequest(BaseModel):
    date: str


@router.post("/chat/agent/confirm", response_model=AgentChatResult)
async def chat_agent_confirm(payload: ConfirmRequest):
    """Apply a pending major Proposal (version + TTL checked server-side)."""
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return confirm_proposal(d)
