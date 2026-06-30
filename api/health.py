"""
Health data ingestion + retrieval.

Current source: manual entry via SleepInputModal (frontend) → POST /health.
Schedules read the stored snapshot per date (agents/nodes.fetch_health_node).

# ── Future: native Apple Health ingestion ──────────────────────────────────
# The iPhone Shortcuts import path (GET /health/import*) was removed — the
# network/firewall/DHCP setup was too flaky to rely on long-term.
#
# When we add a real Apple Health source, it should land HERE as a new endpoint
# (e.g. POST /health/import-apple) that maps HealthKit samples → HealthInput and
# funnels through the SAME receive_health() pipeline below, so storage / caching
# / schedule-regeneration behaviour stays identical to manual entry.
#
# Likely shape (see docs/phase3-plan.md Phase B):
#   - HealthKit on-device (native Swift app, post web→Swift migration) → POSTs us
#   - or a Health Auto Export webhook → POST /health/import-apple
# Until then, manual entry is the only active path.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.nodes import _health_cache
from storage import health_store, save_health_store
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


@router.get("/health/{date}", response_model=HealthSnapshot)
async def get_health(date: str):
    from datetime import date as date_type
    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    snapshot = health_store.get(d)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No health data for this date.")
    return snapshot


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

    health_store[d] = snapshot
    # Invalidate cached health for this date so it's recomputed next time
    _health_cache.pop(d, None)
    save_health_store()

    return snapshot
