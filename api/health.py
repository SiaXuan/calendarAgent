import socket
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agents import orchestrator
from models.health import HealthSnapshot, SleepData

router = APIRouter()


def _lan_ip() -> str:
    """Best-effort local network IP (not 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


class HealthInput(BaseModel):
    date: str                           # YYYY-MM-DD
    sleep_start: datetime
    sleep_end: datetime
    resting_heart_rate: int | None = None
    hrv: float | None = None
    steps: int | None = None
    active_minutes: int | None = None


@router.get("/health/import-simple", response_model=HealthSnapshot)
async def import_simple(
    sleep_time: str = Query(..., description="Bedtime HH:MM e.g. 23:00"),
    wake_time: str = Query(..., description="Wake time HH:MM e.g. 07:30"),
    hr: int | None = Query(default=None),
    hrv: float | None = Query(default=None),
    steps: int | None = Query(default=None),
    active_minutes: int | None = Query(default=None),
):
    """
    Simplified GET endpoint for iPhone Shortcuts.
    Only needs HH:MM times — the backend figures out the correct calendar dates.
    Bedtime >= 12:00 is treated as last night; < 12:00 as early this morning.
    """
    from datetime import date as date_type, timedelta
    today = date_type.today()
    try:
        sh, sm = map(int, sleep_time.split(":"))
        wh, wm = map(int, wake_time.split(":"))
    except ValueError:
        raise HTTPException(status_code=422, detail="Times must be HH:MM format")

    sleep_date = today - timedelta(days=1) if sh >= 12 else today
    sleep_start_dt = datetime(sleep_date.year, sleep_date.month, sleep_date.day, sh, sm)
    sleep_end_dt   = datetime(today.year, today.month, today.day, wh, wm)

    payload = HealthInput(
        date=today.isoformat(),
        sleep_start=sleep_start_dt,
        sleep_end=sleep_end_dt,
        resting_heart_rate=hr,
        hrv=hrv,
        steps=steps,
        active_minutes=active_minutes,
    )
    return await receive_health(payload)


@router.get("/health/import-url")
async def get_import_url():
    """
    Returns the URL template for setting up an iPhone Shortcut.
    Uses the Mac's LAN IP so the iPhone can reach the server over WiFi.
    """
    ip = _lan_ip()
    base = f"http://{ip}:8000"
    # Use the simpler endpoint — only HH:MM times needed, no ISO date formatting
    template = (
        f"{base}/health/import-simple"
        "?sleep_time={{SleepTime}}&wake_time={{WakeTime}}"
        "&hr={{HR}}&hrv={{HRV}}&steps={{Steps}}&active_minutes={{ActiveMin}}"
    )
    return {"server": base, "url_template": template, "lan_ip": ip}


@router.get("/health/import", response_model=HealthSnapshot)
async def import_from_shortcut(
    date: str = Query(..., description="YYYY-MM-DD"),
    sleep_start: str = Query(..., description="ISO datetime e.g. 2026-04-12T23:00:00"),
    sleep_end: str = Query(..., description="ISO datetime e.g. 2026-04-13T07:30:00"),
    hr: int | None = Query(default=None, description="Resting heart rate (bpm)"),
    hrv: float | None = Query(default=None, description="HRV SDNN (ms)"),
    steps: int | None = Query(default=None, description="Step count"),
    active_minutes: int | None = Query(default=None, description="Active/exercise minutes"),
):
    """
    GET endpoint for Apple Shortcuts (macOS) to call via 'Get Contents of URL'.
    Shortcuts can build this URL from Health app samples and trigger it with one tap.

    Example URL Shortcuts generates:
      http://localhost:8000/health/import
        ?date=2026-04-13
        &sleep_start=2026-04-12T23:00:00
        &sleep_end=2026-04-13T07:30:00
        &hr=58&hrv=42&steps=8500&active_minutes=35
    """
    try:
        sleep_start_dt = datetime.fromisoformat(sleep_start)
        sleep_end_dt = datetime.fromisoformat(sleep_end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: {exc}")

    payload = HealthInput(
        date=date,
        sleep_start=sleep_start_dt,
        sleep_end=sleep_end_dt,
        resting_heart_rate=hr,
        hrv=hrv,
        steps=steps,
        active_minutes=active_minutes,
    )
    return await receive_health(payload)


@router.get("/health/{date}", response_model=HealthSnapshot)
async def get_health(date: str):
    from datetime import date as date_type
    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
    snapshot = orchestrator.health_store.get(d)
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

    orchestrator.health_store[d] = snapshot
    # Invalidate cached health for this date so it's recomputed next time
    orchestrator._health_cache.pop(d, None)
    orchestrator.save_health_store()

    return snapshot
