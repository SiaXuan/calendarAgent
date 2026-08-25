"""Email triage (Phase D) — the graceful not-connected path + endpoints.

The connected (real MCP server) path is validated on-device once an Outlook/Gmail
server is attached; here we only cover the no-server behavior + wiring."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

import graphs.email_triage as et
from main import app


@pytest.fixture
def client(clean_stores):
    with TestClient(app) as c:
        yield c


async def test_triage_not_connected_when_no_mail_tools(clean_stores, monkeypatch):
    monkeypatch.setattr(et, "get_mcp_tools", lambda: [])
    r = await et.run_email_triage(date(2026, 8, 25), "en")
    assert r.connected is False
    assert "No mailbox" in (r.note or "")


async def test_triage_endpoint_returns_report(client):
    r = client.post("/email/triage", json={"date": "2026-08-25"})
    assert r.status_code == 200
    assert r.json()["connected"] is False


async def test_chat_history_endpoint_returns_todays_thread(client, clean_stores):
    from storage import chat_sessions
    chat_sessions[date(2026, 8, 25)] = [
        {"role": "user", "content": "把午休移到一点"},
        {"role": "assistant", "content": "好的，已经移到 13:00。"},
    ]
    r = client.get("/chat/agent/history?date=2026-08-25")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "把午休移到一点"
