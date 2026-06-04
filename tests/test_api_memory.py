"""FastAPI route tests for Phase C.2 Memory Inspector endpoints."""
import pytest
from fastapi.testclient import TestClient

from main import app
from memory import store as mem
from models.memory import MemoryNamespace


@pytest.fixture
def client(clean_stores):
    with TestClient(app) as c:
        yield c


# ─── GET /memory ────────────────────────────────────────────────────────────

def test_list_empty(client):
    r = client.get("/memory")
    assert r.status_code == 200
    assert r.json() == []


def test_list_filtered_by_namespace(client):
    mem.add(MemoryNamespace.schedule_prefs, content="a", confidence=0.7)
    mem.add(MemoryNamespace.task_lexicon, content="b", confidence=0.7)
    r = client.get("/memory", params={"namespace": "schedule_prefs"})
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 1
    assert body[0]["content"] == "a"


def test_list_respects_min_confidence(client):
    mem.add(MemoryNamespace.schedule_prefs, content="weak", confidence=0.3)
    mem.add(MemoryNamespace.schedule_prefs, content="strong", confidence=0.85)
    r = client.get("/memory", params={"min_confidence": 0.5})
    body = r.json()
    assert len(body) == 1
    assert body[0]["content"] == "strong"


def test_list_can_exclude_unverified(client):
    mem.add(MemoryNamespace.schedule_prefs, content="raw", confidence=0.7,
            user_verified=False)
    mem.add(MemoryNamespace.schedule_prefs, content="verified", confidence=0.7,
            user_verified=True)
    r = client.get("/memory", params={"include_unverified": False})
    body = r.json()
    assert len(body) == 1
    assert body[0]["content"] == "verified"


# ─── GET /memory/{id} ───────────────────────────────────────────────────────

def test_get_404_for_unknown(client):
    r = client.get("/memory/ghost-id")
    assert r.status_code == 404


def test_get_returns_memory(client):
    created = mem.add(MemoryNamespace.physiological, content="HRV 58", confidence=0.8)
    r = client.get(f"/memory/{created.id}")
    assert r.status_code == 200
    assert r.json()["content"] == "HRV 58"


# ─── POST /memory ───────────────────────────────────────────────────────────

def test_create_memory(client):
    r = client.post("/memory", json={
        "namespace": "task_lexicon",
        "content": "'compile bibliography' = admin, ~30min",
        "confidence": 0.7,
        "structured": {"task_kind": "admin", "duration_min": 30},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["content"].startswith("'compile bibliography'")
    assert body["namespace"] == ["default", "task_lexicon"]
    assert body["user_verified"] is True   # manual entries default to verified
    assert body["structured"]["duration_min"] == 30


def test_create_rejects_empty_content(client):
    r = client.post("/memory", json={
        "namespace": "schedule_prefs",
        "content": "",
        "confidence": 0.7,
    })
    assert r.status_code == 422


def test_create_rejects_out_of_range_confidence(client):
    r = client.post("/memory", json={
        "namespace": "schedule_prefs",
        "content": "x",
        "confidence": 2.0,
    })
    assert r.status_code == 422


# ─── PATCH /memory/{id} ─────────────────────────────────────────────────────

def test_patch_updates_fields(client):
    created = mem.add(MemoryNamespace.schedule_prefs, content="old", confidence=0.5)
    r = client.patch(f"/memory/{created.id}", json={
        "content": "new",
        "user_verified": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "new"
    assert body["user_verified"] is True
    # confidence untouched
    assert body["confidence"] == 0.5


def test_patch_404_for_unknown(client):
    r = client.patch("/memory/ghost", json={"content": "x"})
    assert r.status_code == 404


# ─── DELETE /memory/{id} ────────────────────────────────────────────────────

def test_delete_removes(client):
    created = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.5)
    r = client.delete(f"/memory/{created.id}")
    assert r.status_code == 200
    # Subsequent GET returns 404
    r2 = client.get(f"/memory/{created.id}")
    assert r2.status_code == 404


def test_delete_404_for_unknown(client):
    r = client.delete("/memory/ghost")
    assert r.status_code == 404


# ─── POST /memory/decay ─────────────────────────────────────────────────────

def test_decay_endpoint_returns_counts(client):
    mem.add(MemoryNamespace.schedule_prefs, content="strong", confidence=0.9)
    mem.add(MemoryNamespace.schedule_prefs, content="weak", confidence=0.31)
    r = client.post("/memory/decay", params={"weeks_elapsed": 1.0})
    assert r.status_code == 200
    body = r.json()
    # The weak one (0.31 - 0.05 = 0.26 < 0.3) gets archived
    assert body["archived"] == 1
    assert body["decayed"] == 1


def test_decay_rejects_non_positive_weeks(client):
    r = client.post("/memory/decay", params={"weeks_elapsed": 0})
    assert r.status_code == 422
