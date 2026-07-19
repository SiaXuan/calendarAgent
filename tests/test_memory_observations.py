"""
Tests for memory/observations.py — Phase C.3 N-gate + promotion logic.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from main import app
from memory import observations as obs
from memory import retrieval, store as mem
from models.memory import MemoryNamespace, hour_to_bucket
from storage import memory_store, observation_log


# ─── hour_to_bucket ─────────────────────────────────────────────────────────

class TestHourToBucket:
    def test_morning(self):
        assert hour_to_bucket(7) == "morning"
        assert hour_to_bucket(10) == "morning"

    def test_midday(self):
        assert hour_to_bucket(12) == "midday"

    def test_afternoon(self):
        assert hour_to_bucket(15) == "afternoon"

    def test_evening(self):
        assert hour_to_bucket(19) == "evening"

    def test_night(self):
        assert hour_to_bucket(2) == "night"
        assert hour_to_bucket(23) == "night"


# ─── record ─────────────────────────────────────────────────────────────────

def test_record_appends_observation(clean_stores):
    o = obs.record("accept", "t1::Work", hour=9, task_kind="analytical", cognitive_load="deep")
    assert o.action == "accept"
    assert o.hour_bucket == "morning"
    assert o.task_kind == "analytical"
    assert len(observation_log) == 1


# ─── promote: N-gate ────────────────────────────────────────────────────────

def test_below_threshold_does_not_promote(clean_stores):
    """2 same-direction observations should NOT yield a memory."""
    for _ in range(2):
        obs.record("accept", "t1::Work", hour=9, task_kind="analytical")
    promoted = obs.promote()
    assert promoted == []
    assert len(memory_store) == 0


def test_threshold_reached_promotes_to_memory(clean_stores):
    for _ in range(3):
        obs.record("accept", "t1::Work", hour=9, task_kind="analytical")
    promoted = obs.promote()
    assert len(promoted) == 1
    m = promoted[0]
    assert m.namespace == ("default", "schedule_prefs")
    assert m.structured["action"] == "accept"
    assert m.structured["task_kind"] == "analytical"
    assert m.structured["hour_bucket"] == "morning"
    assert m.structured["observation_count"] == 3
    # Confidence per the curve: 0.4 + 3 * 0.15 = 0.85
    assert abs(m.confidence - 0.85) < 1e-6


def test_different_buckets_count_independently(clean_stores):
    """3 morning + 2 evening = 1 promotion (morning only)."""
    for _ in range(3):
        obs.record("accept", "t1::Work", hour=9, task_kind="analytical")
    for _ in range(2):
        obs.record("accept", "t1::Work", hour=19, task_kind="analytical")
    promoted = obs.promote()
    assert len(promoted) == 1
    assert promoted[0].structured["hour_bucket"] == "morning"


def test_dismiss_and_accept_promote_separately(clean_stores):
    """User can have BOTH an 'accept morning analytical' AND 'dismiss evening analytical' memory."""
    for _ in range(3):
        obs.record("accept", "t1::W", hour=9, task_kind="analytical")
    for _ in range(3):
        obs.record("dismiss", "t2::W", hour=19, task_kind="analytical")
    promoted = obs.promote()
    actions = {p.structured["action"] for p in promoted}
    assert actions == {"accept", "dismiss"}


def test_promote_reinforces_existing_memory_instead_of_duplicating(clean_stores):
    """Re-running promote with more observations updates the same memory."""
    for _ in range(3):
        obs.record("accept", "t1::W", hour=9, task_kind="analytical")
    first = obs.promote()
    assert len(first) == 1
    original_id = first[0].id

    # Two more observations → 5 total
    for _ in range(2):
        obs.record("accept", "t1::W", hour=9, task_kind="analytical")
    second = obs.promote()
    assert len(second) == 1
    assert second[0].id == original_id   # same memory, not a duplicate
    assert second[0].structured["observation_count"] == 5
    # Confidence climbed: 0.4 + 5 * 0.15 = 1.15 → capped at 0.9
    assert second[0].confidence == 0.9
    # Only 1 row in the store
    assert len(memory_store) == 1


def test_observations_older_than_window_ignored(clean_stores):
    """Ancient observations don't push patterns over the threshold."""
    # Plant 3 ancient observations
    for _ in range(3):
        o = obs.record("accept", "t1::W", hour=9, task_kind="analytical")
        # Force timestamps backwards
        observation_log[-1] = o.model_copy(update={
            "timestamp": datetime.now(timezone.utc) - timedelta(days=30),
        })
    promoted = obs.promote()
    assert promoted == []


def test_null_task_kind_treated_consistently(clean_stores):
    """3 observations with task_kind=None still promote (under the '*' bucket)."""
    for _ in range(3):
        obs.record("dismiss", "t1::W", hour=21, task_kind=None)
    promoted = obs.promote()
    assert len(promoted) == 1
    assert promoted[0].structured["task_kind"] is None


# ─── retrieval ─────────────────────────────────────────────────────────────

def test_retrieval_returns_only_high_confidence(clean_stores):
    """for_task_ranking respects the PROD_CONFIDENCE_FLOOR (0.6)."""
    mem.add(MemoryNamespace.schedule_prefs, content="strong signal", confidence=0.8)
    mem.add(MemoryNamespace.schedule_prefs, content="weak signal", confidence=0.4)
    bullets = retrieval.for_task_ranking()
    assert "strong signal" in bullets
    assert "weak signal" not in bullets


def test_retrieval_max_items(clean_stores):
    for i in range(15):
        mem.add(MemoryNamespace.schedule_prefs, content=f"item {i}", confidence=0.7)
    bullets = retrieval.for_task_ranking(max_items=5)
    assert len(bullets) == 5


def test_retrieval_for_chat_pulls_two_namespaces(clean_stores):
    mem.add(MemoryNamespace.schedule_prefs, content="schedule pref", confidence=0.7)
    mem.add(MemoryNamespace.task_lexicon, content="task lexicon", confidence=0.7)
    mem.add(MemoryNamespace.physiological, content="hrv baseline", confidence=0.7)
    bullets = retrieval.for_chat()
    assert "schedule pref" in bullets
    assert "task lexicon" in bullets
    assert "hrv baseline" not in bullets   # not pulled into chat


# ─── /memory/feedback endpoint ──────────────────────────────────────────────

@pytest.fixture
def client(clean_stores):
    with TestClient(app) as c:
        yield c


def test_feedback_endpoint_below_threshold_returns_no_promotions(client):
    r = client.post("/memory/feedback", json={
        "action": "accept",
        "block_key": "t1::W",
        "hour": 9,
        "task_kind": "analytical",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["promoted"] == []
    assert body["observation"]["hour_bucket"] == "morning"


def test_feedback_endpoint_third_signal_promotes(client):
    payload = {
        "action": "accept", "block_key": "t1::W", "hour": 9, "task_kind": "analytical",
    }
    client.post("/memory/feedback", json=payload)
    client.post("/memory/feedback", json=payload)
    r = client.post("/memory/feedback", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert len(body["promoted"]) == 1
    assert body["promoted"][0]["confidence"] > 0


def test_feedback_endpoint_validates_hour(client):
    r = client.post("/memory/feedback", json={
        "action": "accept",
        "block_key": "t1::W",
        "hour": 25,   # invalid
    })
    assert r.status_code == 422


def test_feedback_endpoint_validates_action(client):
    r = client.post("/memory/feedback", json={
        "action": "maybe",     # not in {accept, dismiss}
        "block_key": "t1::W",
        "hour": 9,
    })
    assert r.status_code == 422


# ─── End-to-end: rank_tasks_node injects memory ─────────────────────────────

@pytest.mark.asyncio
async def test_rank_tasks_node_passes_memory_to_task_agent(
    clean_stores, mock_sonnet, sample_task, sample_date,
):
    """If a schedule_prefs memory exists, rank_tasks_node forwards it as
    memory_context to task_agent and the system prompt contains it."""
    from agents.nodes import rank_tasks_node
    from agents.task_agent import _LLMSubtask, _LLMSubtaskList
    from models.task import CognitiveLoad, TaskKind
    from models.user import Language

    mem.add(
        MemoryNamespace.schedule_prefs,
        content="User accepts analytical work in the morning (5 observations)",
        confidence=0.85,
    )
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Work",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))

    state = {
        "target_date": sample_date,
        "language": Language.en,
        "tasks": [sample_task],
    }
    patch = await rank_tasks_node(state)
    # The retrieved memory bullets are exposed on state for downstream uses + traces
    assert patch["user_memory"] == [
        "User accepts analytical work in the morning (5 observations)",
    ]
    # The system prompt the LLM saw includes the memory bullet
    system_msg = mock_sonnet._structured.calls[0][0]
    assert system_msg["role"] == "system"
    # content is now a cache_control block list (prompt caching) — extract the text
    sys_content = system_msg["content"]
    sys_text = sys_content if isinstance(sys_content, str) else sys_content[0]["text"]
    assert "KNOWN USER PREFERENCES" in sys_text
    assert "analytical work in the morning" in sys_text
