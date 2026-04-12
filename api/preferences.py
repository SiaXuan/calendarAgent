from fastapi import APIRouter

from models.user import UserPreferences, UserPreferencesUpdate

router = APIRouter()

# In-memory store (Phase 1)
_prefs = UserPreferences()


def get_current_prefs() -> UserPreferences:
    """Return the active preferences. Called by other modules."""
    return _prefs


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences():
    return _prefs


@router.patch("/preferences", response_model=UserPreferences)
async def update_preferences(update: UserPreferencesUpdate):
    global _prefs
    patch = update.model_dump(exclude_unset=True)
    _prefs = _prefs.model_copy(update=patch)
    return _prefs
