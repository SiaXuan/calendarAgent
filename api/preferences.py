import json
from pathlib import Path

from fastapi import APIRouter

from models.user import UserPreferences, UserPreferencesUpdate

router = APIRouter()

_PREFS_FILE = Path(__file__).parent.parent / "data" / "preferences.json"

_prefs = UserPreferences()


def load_preferences() -> None:
    global _prefs
    if not _PREFS_FILE.exists():
        return
    try:
        data = json.loads(_PREFS_FILE.read_text())
        _prefs = UserPreferences.model_validate(data)
    except Exception:
        pass


def _save() -> None:
    try:
        _PREFS_FILE.parent.mkdir(exist_ok=True)
        _PREFS_FILE.write_text(json.dumps(_prefs.model_dump(mode="json")))
    except Exception:
        pass


def get_current_prefs() -> UserPreferences:
    return _prefs


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences():
    return _prefs


@router.patch("/preferences", response_model=UserPreferences)
async def update_preferences(update: UserPreferencesUpdate):
    global _prefs
    patch = update.model_dump(exclude_unset=True)
    _prefs = _prefs.model_copy(update=patch)
    _save()
    return _prefs
