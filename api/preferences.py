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
    patch = update.model_dump(mode="json", exclude_unset=True)
    # Re-validate the merged dict (not model_copy) so nested configs like
    # custom_mcp_servers coerce back into their models rather than staying dicts.
    _prefs = UserPreferences.model_validate({**_prefs.model_dump(mode="json"), **patch})
    _save()
    if "custom_mcp_servers" in patch:
        from graphs.user_mcp import load_mcp_tools
        await load_mcp_tools()   # apply server changes without a restart
    return _prefs


# ─── MCP server presets ─────────────────────────────────────────────────────

@router.get("/mcp/presets")
async def list_mcp_presets():
    """The MCP servers worth attaching, and whether their credentials are set.

    Attaching one is already possible by hand (PATCH /preferences with a
    custom_mcp_servers entry). This exists because the hard part isn't the
    config, it's knowing which server is the real one — searching for a Canvas
    MCP server returns a page of identical forks of the same project — and which
    of its tools are safe to put on the agent's belt.
    """
    from graphs.user_mcp import get_mcp_tools
    from integrations.mcp.presets import PRESETS

    attached = {s.name for s in _prefs.custom_mcp_servers}
    return {
        "presets": [
            {
                "key": p.key,
                "title": p.title,
                "repo": p.repo,
                "summary": p.summary,
                "env_required": list(p.env_required),
                "env_missing": p.missing_env(),
                "ready": p.is_ready(),
                "attached": p.key in attached,
                "tool_allowlist": list(p.tool_allowlist),
                "excluded_unsafe_tools": list(p.unsafe_tools),
                "notes": p.notes,
            }
            for p in PRESETS.values()
        ],
        "tools_loaded": [t.name for t in get_mcp_tools()],
    }


@router.post("/mcp/presets/{key}/attach")
async def attach_mcp_preset(key: str):
    """Add a preset to `custom_mcp_servers` and reload the agent's tool belt.

    Refuses while credentials are missing: attaching then would just log a
    failed load on every reload and leave the belt silently short. The response
    names the variables to set.
    """
    from fastapi import HTTPException

    from graphs.user_mcp import load_mcp_tools
    from integrations.mcp.presets import PRESETS

    preset = PRESETS.get(key)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"No MCP preset {key!r}.")
    if not preset.is_ready():
        raise HTTPException(status_code=409, detail={
            "code": "credentials_missing",
            "message": f"Set {', '.join(preset.missing_env())} in .env first.",
            "env_missing": preset.missing_env(),
            "notes": preset.notes,
        })

    _prefs.custom_mcp_servers = [
        s for s in _prefs.custom_mcp_servers if s.name != key
    ] + [preset.build()]
    _save()
    await load_mcp_tools()

    from graphs.user_mcp import get_mcp_tools
    return {"attached": key, "tools_loaded": [t.name for t in get_mcp_tools()]}


@router.post("/mcp/presets/{key}/detach")
async def detach_mcp_preset(key: str):
    """Remove a preset's server and reload the belt."""
    from graphs.user_mcp import get_mcp_tools, load_mcp_tools

    before = len(_prefs.custom_mcp_servers)
    _prefs.custom_mcp_servers = [s for s in _prefs.custom_mcp_servers if s.name != key]
    _save()
    await load_mcp_tools()
    return {
        "detached": key,
        "was_attached": len(_prefs.custom_mcp_servers) < before,
        "tools_loaded": [t.name for t in get_mcp_tools()],
    }
