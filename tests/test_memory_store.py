"""
Tests for memory/store.py — Phase C.1 storage layer.

Pure CRUD; no LLM, no embeddings.
"""
from datetime import datetime, timedelta, timezone

import pytest

from memory import store as mem
from models.memory import MemoryNamespace, MemoryUpdate


@pytest.fixture
def isolated_memory(clean_stores):
    """clean_stores already wipes the in-memory dict and isolates _DATA_DIR."""
    yield


# ─── add / get ───────────────────────────────────────────────────────────────

def test_add_creates_a_memory(isolated_memory):
    m = mem.add(
        MemoryNamespace.schedule_prefs,
        content="prefers analytical work mornings",
        confidence=0.7,
    )
    assert m.id
    assert m.namespace == ("default", "schedule_prefs")
    assert m.content == "prefers analytical work mornings"
    assert m.confidence == 0.7
    assert m.user_verified is False
    # Round-trip via get()
    fetched = mem.get(m.id)
    assert fetched is not None
    assert fetched.id == m.id


def test_add_clamps_confidence(isolated_memory):
    high = mem.add(MemoryNamespace.physiological, content="x", confidence=1.5)
    low = mem.add(MemoryNamespace.physiological, content="y", confidence=-0.3)
    assert high.confidence == 1.0
    assert low.confidence == 0.0


def test_get_returns_none_for_unknown(isolated_memory):
    assert mem.get("does-not-exist") is None


# ─── list_by_namespace ──────────────────────────────────────────────────────

def test_list_filters_by_namespace(isolated_memory):
    a = mem.add(MemoryNamespace.schedule_prefs, content="sp", confidence=0.7)
    b = mem.add(MemoryNamespace.task_lexicon, content="tl", confidence=0.7)
    sp = mem.list_by_namespace(MemoryNamespace.schedule_prefs)
    assert [m.id for m in sp] == [a.id]
    tl = mem.list_by_namespace(MemoryNamespace.task_lexicon)
    assert [m.id for m in tl] == [b.id]


def test_list_respects_confidence_floor(isolated_memory):
    mem.add(MemoryNamespace.schedule_prefs, content="weak", confidence=0.3)
    strong = mem.add(MemoryNamespace.schedule_prefs, content="strong", confidence=0.8)
    high = mem.list_by_namespace(
        MemoryNamespace.schedule_prefs, min_confidence=0.6,
    )
    assert [m.id for m in high] == [strong.id]


def test_list_sorted_by_last_reinforced_desc(isolated_memory):
    older = mem.add(MemoryNamespace.task_lexicon, content="old", confidence=0.7)
    newer = mem.add(MemoryNamespace.task_lexicon, content="new", confidence=0.7)
    # Force `older` to look older by mutating its timestamp
    from storage import memory_store
    memory_store[older.id] = older.model_copy(update={
        "last_reinforced_at": datetime.now(timezone.utc) - timedelta(days=5),
    })
    result = mem.list_by_namespace(MemoryNamespace.task_lexicon)
    assert result[0].id == newer.id   # most-recent first
    assert result[1].id == older.id


def test_list_all_namespaces_when_no_filter(isolated_memory):
    mem.add(MemoryNamespace.schedule_prefs, content="a", confidence=0.7)
    mem.add(MemoryNamespace.physiological, content="b", confidence=0.7)
    assert len(mem.list_by_namespace(None)) == 2


# ─── update / reinforce ─────────────────────────────────────────────────────

def test_update_patches_fields(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="orig", confidence=0.5)
    patched = mem.update(m.id, MemoryUpdate(content="edited", user_verified=True))
    assert patched is not None
    assert patched.content == "edited"
    assert patched.user_verified is True
    assert patched.confidence == 0.5   # untouched


def test_update_unknown_returns_none(isolated_memory):
    assert mem.update("ghost", MemoryUpdate(content="x")) is None


def test_reinforce_bumps_confidence_and_resets_timer(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.5)
    # Force last_reinforced_at older
    from storage import memory_store
    memory_store[m.id] = m.model_copy(update={
        "last_reinforced_at": datetime.now(timezone.utc) - timedelta(days=7),
    })
    bumped = mem.reinforce(m.id, confidence_boost=0.1)
    assert bumped is not None
    assert bumped.confidence == 0.6
    # Reinforced timer is now within the last second
    age = datetime.now(timezone.utc) - bumped.last_reinforced_at
    assert age.total_seconds() < 1


def test_reinforce_caps_confidence_at_one(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.95)
    bumped = mem.reinforce(m.id, confidence_boost=0.5)
    assert bumped.confidence == 1.0


# ─── delete ─────────────────────────────────────────────────────────────────

def test_delete_removes(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.5)
    assert mem.delete(m.id) is True
    assert mem.get(m.id) is None


def test_delete_unknown_returns_false(isolated_memory):
    assert mem.delete("ghost") is False


# ─── decay_pass ─────────────────────────────────────────────────────────────

def test_decay_reduces_confidence(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.8)
    result = mem.decay_pass(weeks_elapsed=1.0)
    assert result == {"decayed": 1, "archived": 0}
    after = mem.get(m.id)
    assert after is not None
    assert abs(after.confidence - 0.75) < 1e-6   # 0.8 - 0.05


def test_decay_archives_below_threshold(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.33)
    result = mem.decay_pass(weeks_elapsed=1.0, archive_threshold=0.3)
    assert result == {"decayed": 0, "archived": 1}
    assert mem.get(m.id) is None


def test_decay_skips_user_verified(isolated_memory):
    m = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.5, user_verified=True)
    result = mem.decay_pass(weeks_elapsed=10.0)   # would normally crash to 0
    assert result == {"decayed": 0, "archived": 0}
    assert mem.get(m.id).confidence == 0.5


# ─── garbage_collect_interactions ───────────────────────────────────────────

def test_gc_interactions_drops_old_records(isolated_memory):
    fresh = mem.add(MemoryNamespace.interactions, content="recent", confidence=0.5)
    old = mem.add(MemoryNamespace.interactions, content="ancient", confidence=0.5)
    from storage import memory_store
    memory_store[old.id] = old.model_copy(update={
        "created_at": datetime.now(timezone.utc) - timedelta(days=40),
    })
    removed = mem.garbage_collect_interactions(ttl_days=30)
    assert removed == 1
    assert mem.get(old.id) is None
    assert mem.get(fresh.id) is not None


def test_gc_interactions_leaves_other_namespaces_alone(isolated_memory):
    """An old `schedule_prefs` memory should NOT be GC'd by the interaction sweep."""
    pref = mem.add(MemoryNamespace.schedule_prefs, content="x", confidence=0.7)
    from storage import memory_store
    memory_store[pref.id] = pref.model_copy(update={
        "created_at": datetime.now(timezone.utc) - timedelta(days=100),
    })
    removed = mem.garbage_collect_interactions(ttl_days=30)
    assert removed == 0
    assert mem.get(pref.id) is not None


# ─── persistence roundtrip ──────────────────────────────────────────────────

def test_persistence_roundtrip(isolated_memory):
    """add() writes to disk; load_memory_store() reads it back."""
    import storage
    m = mem.add(MemoryNamespace.physiological, content="HRV baseline 58",
                confidence=0.8, structured={"hrv": 58})
    # Wipe in-memory then reload
    storage.memory_store.clear()
    storage.load_memory_store()
    loaded = mem.get(m.id)
    assert loaded is not None
    assert loaded.content == "HRV baseline 58"
    assert loaded.structured == {"hrv": 58}
