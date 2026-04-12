from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents import orchestrator
from models.health import HealthSnapshot, SleepData

router = APIRouter()


class HealthInput(BaseModel):
    date: str                           # YYYY-MM-DD
    sleep_start: datetime
    sleep_end: datetime
    resting_heart_rate: int | None = None
    hrv: float | None = None
    steps: int | None = None
    active_minutes: int | None = None


@router.post("/health", response_model=HealthSnapshot)
async def receive_health(payload: HealthInput):
    from datetime import date as date_type
    try:
        d = date_type.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    duration = (payload.sleep_end - payload.sleep_start).total_seconds() / 3600

    snapshot = HealthSnapshot(
        date=d,
        sleep=SleepData(
            duration_hours=round(duration, 2),
            sleep_start=payload.sleep_start,
            sleep_end=payload.sleep_end,
        ),
        resting_heart_rate=payload.resting_heart_rate,
        hrv=payload.hrv,
        steps=payload.steps,
        active_minutes=payload.active_minutes,
        submitted_at=datetime.now(),
    )

    orchestrator.health_store[d] = snapshot
    # Invalidate cached health for this date so it's recomputed next time
    orchestrator._health_cache.pop(d, None)

    return snapshot
