from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from graphs.agent_run import (
    AgentChatResult,
    confirm_proposal,
    dismiss_proposal,
    get_chat_history,
    run_chat_agent,
    undo_last_commit,
)

# Retired POST /chat also needed:
#   from graphs.adjust_graph import run_adjust_graph
#   from models.schedule import DaySchedule
#   from storage import schedule_store

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    date: str   # YYYY-MM-DD


# ─── Retired: POST /chat ─────────────────────────────────────────────────────
# The pre-ReAct adjustment path: one structured-output call filling a fixed
# 6-field schema (`agents/chat_agent.py` via `graphs/adjust_graph.py`). Every
# client moved to /chat/agent — the Swift app and the shelved web UI both call
# only that — so this route was reachable but never reached.
#
# Route unregistered 2026-09-07; the modules behind it stay (still imported by
# `scripts/visualize_graphs.py` and covered by tests) so the fallback can be
# re-registered by uncommenting if the agent path ever needs a dumb backstop.
#
# @router.post("/chat", response_model=DaySchedule)
# async def chat(payload: ChatRequest):
#     """Legacy fixed-schema adjustment (fallback). New UI uses /chat/agent."""
#     try:
#         d = date.fromisoformat(payload.date)
#     except ValueError:
#         raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
#
#     if schedule_store.get(d) is None:
#         raise HTTPException(
#             status_code=404,
#             detail=f"No schedule for {payload.date}. Generate one first via POST /schedule/generate.",
#         )
#
#     updated, _params = await run_adjust_graph(d, payload.message)
#     return updated


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


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatHistory(BaseModel):
    messages: list[ChatTurn]


@router.get("/chat/agent/history", response_model=ChatHistory)
async def chat_agent_history(date_str: str = Query(alias="date")):
    """Today's conversation so the frontend can restore the thread on reopen."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return ChatHistory(messages=[ChatTurn(**t) for t in get_chat_history(d)])


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


@router.post("/chat/agent/dismiss", response_model=AgentChatResult)
async def chat_agent_dismiss(payload: ConfirmRequest):
    """Drop a pending Proposal (user chose 'Keep as is'); logs the reject label."""
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return dismiss_proposal(d)


@router.post("/chat/agent/undo", response_model=AgentChatResult)
async def chat_agent_undo(payload: ConfirmRequest):
    """Step back to the arrangement from before the last agent commit.

    Refuses (terminal_state=clarification) if the day changed after that commit.
    """
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return undo_last_commit(d)
