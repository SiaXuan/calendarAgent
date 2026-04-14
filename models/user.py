from enum import Enum
from pydantic import BaseModel, Field


class Language(str, Enum):
    en = "en"
    zh_CN = "zh-CN"
    zh_TW = "zh-TW"
    ja = "ja"


class UserPreferences(BaseModel):
    language: Language = Language.zh_CN
    work_start: int = Field(default=8, ge=0, le=23)
    work_end: int = Field(default=22, ge=1, le=24)
    max_deep_work_minutes: int = Field(default=90, ge=15, le=180)
    auto_generate_on_health_sync: bool = True
    auto_write_to_calendar: bool = False


class UserPreferencesUpdate(BaseModel):
    """Partial-update body for PATCH /preferences."""
    language: Language | None = None
    work_start: int | None = Field(default=None, ge=0, le=23)
    work_end: int | None = Field(default=None, ge=1, le=24)
    max_deep_work_minutes: int | None = Field(default=None, ge=15, le=180)
    auto_generate_on_health_sync: bool | None = None
    auto_write_to_calendar: bool | None = None
