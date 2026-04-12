from datetime import date, datetime
from pydantic import BaseModel, Field


class SleepData(BaseModel):
    duration_hours: float
    sleep_start: datetime
    sleep_end: datetime
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)


class HealthSnapshot(BaseModel):
    date: date
    sleep: SleepData
    resting_heart_rate: int | None = None
    hrv: float | None = None          # milliseconds
    steps: int | None = None
    active_minutes: int | None = None
    submitted_at: datetime = Field(default_factory=datetime.now)
