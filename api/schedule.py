import asyncio
import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agents.calendar_reconcile import reconcile_schedule
from agents.calendar_writeback import (
    write_block_to_calendar as _writeback_block,
    write_schedule_to_calendar as _writeback_schedule,
)
from agents.scheduler_agent import find_nearest_slot
from api.preferences import get_current_prefs
from graphs.schedule_graph import reflow_after_pin, run_schedule_graph
from graphs.schedule_stream import stream_schedule_events
from integrations.caldav_client import fetch_debug_info
from models.project import CompletionStatus
from models.schedule import BlockType, DaySchedule
from storage import PinSpec, completion_store, schedule_store, subtask_pins

router = APIRouter()


class CalendarEvent(BaseModel):
    """One event the frontend read from the local calendar via EventKit. `start`
    /`end` are ISO datetimes; `description` (event notes) carries agent tags so
    the backend can tell its own events from user-added ones."""
    title: str | None = None
    start: str
    end: str
    description: str | None = None


class GenerateRequest(BaseModel):
    date: str   # YYYY-MM-DD
    # Local/EventKit path (docs/ARCHITECTURE.md §0): the day's fixed events read
    # by the frontend. Omit (null) to fall back to the backend reading CalDAV.
    calendar_events: list[CalendarEvent] | None = None


@router.post("/schedule/generate", response_model=DaySchedule)
async def generate_schedule(payload: GenerateRequest):
    try:
        d = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    events = (
        [e.model_dump() for e in payload.calendar_events]
        if payload.calendar_events is not None else None
    )
    schedule = await run_schedule_graph(d, calendar_events=events)
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
            async for event in stream_schedule_events(d):
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

    schedule = schedule_store.get(d)
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

    if schedule_store.get(d) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule cached for {target_date}. Generate it first.",
        )

    result = await _writeback_schedule(d)
    # Total failure (not configured / calendar unreadable / every block failed
    # with nothing written or deleted) → surface as an error instead of a silent
    # {written: 0}. Partial failures return 200 with the `failed` list so the
    # client can show which blocks didn't sync.
    if not result.get("ok", True) and result.get("written", 0) == 0 and result.get("deleted", 0) == 0:
        raise HTTPException(status_code=502, detail=result)
    return result


class CurrentEvent(BaseModel):
    """One agent-owned event the frontend currently sees on the calendar for the
    day (read via EventKit; identified by its per-block tag in the notes)."""
    tag_key: str
    title: str | None = None
    start: str | None = None
    end: str | None = None


class ChangesetRequest(BaseModel):
    current_events: list[CurrentEvent] = []


@router.post("/schedule/{target_date}/changeset")
async def schedule_changeset(target_date: str, payload: ChangesetRequest):
    """
    Local/EventKit path (see docs/ARCHITECTURE.md §0): the backend does NOT touch
    the calendar. The frontend uploads the current agent events; we diff them
    against the cached schedule and return a change-set {create, update, delete}
    for the frontend to apply via EventKit. Completed blocks are skipped (they
    live as history), never re-created.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    schedule = schedule_store.get(d)
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule cached for {target_date}. Generate it first.",
        )
    done_keys = {
        k for k, r in completion_store.items() if r.status == CompletionStatus.done
    }
    return reconcile_schedule(
        schedule.blocks,
        [e.model_dump() for e in payload.current_events],
        done_keys=done_keys,
    )


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

    schedule = schedule_store.get(d)
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

    result = await _writeback_block(d, target)
    if not result.get("ok", True) and result.get("written", 0) == 0 and result.get("deleted", 0) == 0:
        raise HTTPException(status_code=502, detail=result)
    return result


# ─── Pin endpoints (drag-to-move + pomodoro +/-) ────────────────────────────

class PinRequest(BaseModel):
    """
    Either field is optional — defaults come from the currently-scheduled block:
      * start_iso omitted → keep current start (resize only)
      * duration_min omitted → keep current duration (move only)
    block_key is "{task_id}::{title}" (matches the frontend's blockKey).
    """
    block_key: str
    start_iso: str | None = None
    duration_min: int | None = None


class PinResponse(BaseModel):
    block_key: str
    start: str
    duration_min: int
    adjusted: bool   # True if conflict resolution moved the pin from the requested start
    schedule: DaySchedule


@router.post("/schedule/{target_date}/pin", response_model=PinResponse)
async def pin_block(target_date: str, payload: PinRequest):
    """
    Pin a subtask to a specific (start, duration). The scheduler treats pinned
    subtasks as fixed blocks on the next run, so the rest of the day reflows
    around them.

    If the requested start collides with an existing fixed/meal block, the pin
    is snapped to the nearest available slot inside work hours; `adjusted=True`
    in the response so the UI can show feedback.
    """
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    current = schedule_store.get(d)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule for {target_date}. Generate one first.",
        )

    # Find the live block matching this key so we can default start/duration.
    target_block = next(
        (b for b in current.blocks
         if b.task_id and f"{b.task_id}::{b.title}" == payload.block_key),
        None,
    )
    if target_block is None:
        raise HTTPException(
            status_code=404,
            detail=f"No block with key={payload.block_key!r} in current schedule.",
        )

    # Resolve start: requested OR current.
    if payload.start_iso:
        try:
            requested_start = datetime.fromisoformat(payload.start_iso)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid start_iso datetime.")
    else:
        requested_start = target_block.start

    # Resolve duration: requested OR current.
    if payload.duration_min is not None:
        duration_min = max(5, payload.duration_min)
    else:
        duration_min = max(5, int((target_block.end - target_block.start).total_seconds() // 60))

    # Conflict resolution against fixed + meal blocks ONLY (other scheduled
    # blocks will reflow on the next graph run, so they don't constrain us).
    prefs = get_current_prefs()
    blocking = [
        b for b in current.blocks
        if b.block_type in (BlockType.fixed, BlockType.meal)
        and not (b.task_id and f"{b.task_id}::{b.title}" == payload.block_key)
    ]
    placed_start = find_nearest_slot(
        requested_start, duration_min, blocking, prefs.work_start, prefs.work_end,
    )

    subtask_pins.setdefault(d, {})[payload.block_key] = PinSpec(
        start=placed_start,
        duration_min=duration_min,
    )

    # Lightweight reflow — bypasses LLM/CalDAV/AppleScript (those didn't change)
    new_schedule = await reflow_after_pin(d)
    return PinResponse(
        block_key=payload.block_key,
        start=placed_start.isoformat(),
        duration_min=duration_min,
        adjusted=(placed_start != requested_start),
        schedule=new_schedule,
    )


@router.delete("/schedule/{target_date}/pin/{block_key:path}", response_model=DaySchedule)
async def unpin_block(target_date: str, block_key: str):
    """Release a pin so the subtask can be auto-scheduled again."""
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    pins = subtask_pins.get(d, {})
    if block_key not in pins:
        raise HTTPException(status_code=404, detail=f"No pin for key={block_key!r} on {target_date}.")
    del pins[block_key]
    if not pins:
        subtask_pins.pop(d, None)

    return await reflow_after_pin(d)
