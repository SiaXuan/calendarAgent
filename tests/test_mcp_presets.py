"""
MCP server presets: knowing which server to attach, and attaching it safely.

Attaching a server was already codeless (PATCH /preferences). What presets add
is judgement — which project is the real one, which of its tools belong on the
belt, and which write without asking. These tests pin the parts of that
judgement that must not quietly erode.
"""
import pytest
from fastapi.testclient import TestClient

from integrations.mcp.presets import CANVAS, GOOGLE_WORKSPACE, PRESETS
from main import app


@pytest.fixture
def client(clean_stores):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def prefs_servers():
    """custom_mcp_servers is persisted global state; restore it."""
    from api.preferences import get_current_prefs
    p = get_current_prefs()
    before = list(p.custom_mcp_servers)
    yield p
    p.custom_mcp_servers = before


# ─── the safety invariant ───────────────────────────────────────────────────

@pytest.mark.parametrize("preset", PRESETS.values(), ids=lambda p: p.key)
def test_unsafe_tools_never_reach_the_belt(preset):
    """
    A tool that writes on first call, with no preview, must not be on the belt.

    Canvas ships create_planner_note / mark_planner_item_complete /
    create_personal_calendar_event, which act immediately. Everything else in
    this app that changes user-visible state goes through the impact gate or a
    confirmation; an MCP tool bypasses both.
    """
    assert set(preset.unsafe_tools).isdisjoint(preset.tool_allowlist)


@pytest.mark.parametrize("preset", PRESETS.values(), ids=lambda p: p.key)
def test_every_preset_narrows_the_belt(preset):
    """An empty allowlist means "load all" — fine for a 3-tool server, wrong for
    Canvas (100+) or Workspace. Presets exist partly to prevent that."""
    assert preset.tool_allowlist


@pytest.mark.parametrize("preset", PRESETS.values(), ids=lambda p: p.key)
def test_presets_hold_no_secrets(preset):
    """Credentials are named, never stored; build() reads them at call time.

    Some presets need none at all (the official reference servers), so the
    invariant is "env carries exactly the named variables", not "there are
    variables".
    """
    assert set(preset.build().env) == set(preset.env_required)


# ─── readiness ──────────────────────────────────────────────────────────────

def test_readiness_covers_the_launcher_too(monkeypatch):
    """
    A missing launcher fails silently otherwise: the config looks right and the
    server just never loads. Most Python MCP servers run via `uvx`.
    """
    from integrations.mcp.presets import TIME
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("pathlib.Path.exists", lambda _: False)
    assert not TIME.is_ready()
    assert f"command:{TIME.command}" in TIME.blockers()


def test_credential_free_presets_need_no_env():
    """Time and Fetch are official reference servers — nothing to configure."""
    from integrations.mcp.presets import FETCH, TIME
    assert TIME.env_required == () and FETCH.env_required == ()
    assert TIME.missing_env() == [] and FETCH.missing_env() == []


def test_built_command_is_absolute_when_the_launcher_exists():
    """The backend starts as .venv/bin/uvicorn, which doesn't put the venv on
    PATH — so the config has to carry a resolved path, not a bare name."""
    from integrations.mcp.presets import TIME
    import os
    resolved = TIME.resolved_command()
    if resolved is None:
        import pytest as _pytest
        _pytest.skip("uvx not installed in this environment")
    assert os.path.isabs(TIME.build().command)


def test_preset_is_not_ready_without_its_env(monkeypatch):
    for key in CANVAS.env_required:
        monkeypatch.delenv(key, raising=False)
    assert not CANVAS.is_ready()
    assert set(CANVAS.missing_env()) == set(CANVAS.env_required)


def test_preset_becomes_ready_once_env_is_set(monkeypatch):
    for key in CANVAS.env_required:
        monkeypatch.setenv(key, "x")
    assert CANVAS.is_ready()
    assert CANVAS.missing_env() == []


def test_build_carries_the_allowlist_into_the_server_config(monkeypatch):
    for key in GOOGLE_WORKSPACE.env_required:
        monkeypatch.setenv(key, "x")
    cfg = GOOGLE_WORKSPACE.build()
    assert cfg.name == "google-workspace"
    assert cfg.transport == "stdio"
    assert cfg.tool_allowlist == list(GOOGLE_WORKSPACE.tool_allowlist)


# ─── endpoints ──────────────────────────────────────────────────────────────

def test_listing_reports_what_is_missing(client, monkeypatch):
    for key in CANVAS.env_required:
        monkeypatch.delenv(key, raising=False)
    body = client.get("/mcp/presets").json()
    canvas = next(p for p in body["presets"] if p["key"] == "canvas")
    assert canvas["ready"] is False
    assert set(canvas["env_missing"]) == set(CANVAS.env_required)
    assert canvas["repo"].startswith("https://github.com/")


def test_attach_refuses_while_credentials_are_missing(client, monkeypatch):
    """
    Attaching without credentials would just log a failed load on every reload
    and leave the belt silently short — better to refuse and name the variables.
    """
    for key in CANVAS.env_required:
        monkeypatch.delenv(key, raising=False)
    r = client.post("/mcp/presets/canvas/attach")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "not_ready"
    assert set(detail["env_missing"]) == set(CANVAS.env_required)
    assert "CANVAS_API_TOKEN" in detail["message"]


def test_unknown_preset_is_404(client):
    assert client.post("/mcp/presets/nope/attach").status_code == 404


def test_attach_then_detach_roundtrip(client, monkeypatch, prefs_servers):
    """Attach writes the config; detach takes it back out. The tool load itself
    fails here (no real Canvas server), which is the point — it must not raise."""
    for key in CANVAS.env_required:
        monkeypatch.setenv(key, "x")

    assert client.post("/mcp/presets/canvas/attach").status_code == 200
    assert any(s.name == "canvas" for s in prefs_servers.custom_mcp_servers)

    r = client.post("/mcp/presets/canvas/detach")
    assert r.status_code == 200 and r.json()["was_attached"] is True
    assert not any(s.name == "canvas" for s in prefs_servers.custom_mcp_servers)


def test_attach_is_idempotent(client, monkeypatch, prefs_servers):
    for key in CANVAS.env_required:
        monkeypatch.setenv(key, "x")
    client.post("/mcp/presets/canvas/attach")
    client.post("/mcp/presets/canvas/attach")
    assert sum(s.name == "canvas" for s in prefs_servers.custom_mcp_servers) == 1


def test_detaching_something_not_attached_is_not_an_error(client, prefs_servers):
    prefs_servers.custom_mcp_servers = []
    r = client.post("/mcp/presets/canvas/detach")
    assert r.status_code == 200 and r.json()["was_attached"] is False
