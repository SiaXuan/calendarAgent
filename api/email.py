"""Email triage endpoint (Phase D). The Mail window calls this to pull the agent's
report of today's new mail. Runs over the user's attached email MCP server."""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.preferences import get_current_prefs
from graphs.email_triage import run_email_triage
from models.email import EmailReport

router = APIRouter()


class TriageRequest(BaseModel):
    date: str   # YYYY-MM-DD


@router.post("/email/triage", response_model=EmailReport)
async def triage_email(payload: TriageRequest):
    """Pull + triage today's new mail. Returns a structured report; connected=False
    when no email MCP server is attached (graceful)."""
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    return await run_email_triage(d, language=get_current_prefs().language)
