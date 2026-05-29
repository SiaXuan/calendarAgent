import asyncio
import json
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agents import orchestrator
from integrations.caldav_client import fetch_debug_info
from models.schedule import DaySchedule

router = APIRouter()


class GenerateRequest(BaseModel):
    date: str   # YYYY-MM-DD


@router.post("/schedule/generate", response_model=DaySchedule)
async def generate_schedule(payload: GenerateRequest):
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    schedule = await orchestrator.generate_day_schedule(d)
    return schedule


@router.get("/calendar/debug/{target_date}")
async def debug_calendar(target_date: str):
    """
    Debug endpoint — tests CalDAV connectivity and returns raw events.
    Does NOT use the orchestrator cache, always fetches live.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    return await asyncio.to_thread(fetch_debug_info, d)


@router.get("/schedule/stream/{target_date}")
async def stream_schedule(target_date: str):
    """
    SSE endpoint — streams health → fixed blocks → full schedule as JSON events.
    The frontend renders each stage incrementally as it arrives.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    async def generator():
        try:
            async for event in orchestrator.stream_day_schedule(d):
                yield {"data": json.dumps(event, default=str)}
        except Exception as exc:
            yield {"data": json.dumps({"type": "error", "message": str(exc)})}

    return EventSourceResponse(generator())


@router.get("/schedule/{target_date}", response_model=DaySchedule)
async def get_schedule(target_date: str):
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    schedule = orchestrator.schedule_store.get(d)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"No schedule found for {target_date}.")
    return schedule


@router.post("/schedule/{target_date}/write")
async def write_schedule_to_calendar(target_date: str):
    """
    Write the cached DaySchedule for target_date back to iCloud Calendar.
    Clears previously-written agent blocks first so re-runs don't duplicate.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    if orchestrator.schedule_store.get(d) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule cached for {target_date}. Generate it first.",
        )

    return await orchestrator.write_schedule_to_calendar(d)


class BlockWriteRequest(BaseModel):
    start: str  # ISO datetime — looked up against schedule_store[date].blocks


@router.post("/schedule/{target_date}/blocks/write")
async def write_single_block(target_date: str, payload: BlockWriteRequest):
    """
    Write one block from schedule_store[target_date] to iCloud Calendar.
    Identified by its start datetime (unique per day). Idempotent: a second
    call with the same start replaces the previously-written event.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    schedule = orchestrator.schedule_store.get(d)
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule cached for {target_date}. Generate it first.",
        )

    target = next((b for b in schedule.blocks if b.start.isoformat() == payload.start), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"No block with start={payload.start} in schedule for {target_date}.",
        )

    return await orchestrator.write_block_to_calendar(d, target)
