"""
Memory CRUD layer for Phase C.

Backed by `storage.memory_store` (a plain in-memory dict keyed by memory.id).
Persistence is opportunistic — every mutating helper calls save_memory_store
after success, mirroring how task_store works.

Vector retrieval (LangMem InMemoryStore with embeddings) will be layered on top
during Phase C.3. Right now retrieval is namespace + filter — sufficient for
the Memory Inspector UI and rule-triggered writes.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from models.memory import Memory, MemoryNamespace, MemoryUpdate
from storage import memory_store, save_memory_store


# Default namespace prefix (single-user; keep the slot for multi-user later)
_DEFAULT_USER = "default"

# Confidence below which memories should NOT feed into agent prompts.
# Inspector UI shows everything; agent reads only confidence > MIN_PROD_CONFIDENCE.
MIN_PROD_CONFIDENCE = 0.6

# Interactions namespace has a 30-day TTL — events older than this get garbage-collected.
_INTERACTION_TTL_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ns(bucket: MemoryNamespace) -> tuple[str, str]:
    return (_DEFAULT_USER, bucket.value)


def add(
    bucket: MemoryNamespace,
    content: str,
    *,
    confidence: float,
    structured: dict | None = None,
    source_event_ids: list[str] | None = None,
    user_verified: bool = False,
    decay_rate: float = 0.05,
) -> Memory:
    """Create a new Memory and persist it. Returns the saved object."""
    now = _now()
    mem = Memory(
        id=str(uuid.uuid4()),
        namespace=_ns(bucket),
        content=content,
        structured=structured,
        confidence=max(0.0, min(1.0, confidence)),
        source_event_ids=source_event_ids or [],
        created_at=now,
        last_reinforced_at=now,
        decay_rate=decay_rate,
        user_verified=user_verified,
    )
    memory_store[mem.id] = mem
    save_memory_store()
    return mem


def get(memory_id: str) -> Memory | None:
    return memory_store.get(memory_id)


def list_by_namespace(
    bucket: MemoryNamespace | None = None,
    *,
    min_confidence: float = 0.0,
    include_unverified: bool = True,
) -> list[Memory]:
    """List all memories, optionally filtered by namespace + confidence floor."""
    target_ns = _ns(bucket) if bucket is not None else None
    out: list[Memory] = []
    for m in memory_store.values():
        if target_ns is not None and m.namespace != target_ns:
            continue
        if m.confidence < min_confidence:
            continue
        if not include_unverified and not m.user_verified:
            continue
        out.append(m)
    # Most-recently-reinforced first — matches Inspector UX expectation.
    out.sort(key=lambda m: m.last_reinforced_at, reverse=True)
    return out


def update(memory_id: str, patch: MemoryUpdate) -> Memory | None:
    """Apply an Inspector-driven patch. Bumps last_reinforced_at when user edits."""
    mem = memory_store.get(memory_id)
    if mem is None:
        return None
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        return mem
    updated = mem.model_copy(update={**fields, "last_reinforced_at": _now()})
    memory_store[memory_id] = updated
    save_memory_store()
    return updated


def reinforce(memory_id: str, *, confidence_boost: float = 0.05) -> Memory | None:
    """Bump confidence + reset decay timer when a memory's pattern is observed again."""
    mem = memory_store.get(memory_id)
    if mem is None:
        return None
    new_conf = min(1.0, mem.confidence + confidence_boost)
    updated = mem.model_copy(update={
        "confidence": new_conf,
        "last_reinforced_at": _now(),
    })
    memory_store[memory_id] = updated
    save_memory_store()
    return updated


def delete(memory_id: str) -> bool:
    """Remove a memory. Returns True if deleted, False if it wasn't there."""
    if memory_id not in memory_store:
        return False
    del memory_store[memory_id]
    save_memory_store()
    return True


def decay_pass(*, weeks_elapsed: float = 1.0, archive_threshold: float = 0.3) -> dict:
    """
    Weekly decay sweep (Phase C.4 cron entry point).

    For every memory:
      confidence -= decay_rate * weeks_elapsed  (skipped if user_verified)
    Memories whose confidence drops below `archive_threshold` get deleted
    immediately. The cron job in Phase C.4 will call this once per week.

    Returns {"decayed": N, "archived": M} for telemetry.
    """
    decayed, archived = 0, 0
    for mid in list(memory_store.keys()):
        mem = memory_store[mid]
        if mem.user_verified:
            continue  # user-confirmed memories don't decay
        new_conf = mem.confidence - mem.decay_rate * weeks_elapsed
        if new_conf < archive_threshold:
            del memory_store[mid]
            archived += 1
            continue
        memory_store[mid] = mem.model_copy(update={"confidence": max(0.0, new_conf)})
        decayed += 1
    if decayed or archived:
        save_memory_store()
    return {"decayed": decayed, "archived": archived}


def garbage_collect_interactions(*, ttl_days: int = _INTERACTION_TTL_DAYS) -> int:
    """
    Drop episodic logs in the `interactions` namespace older than ttl_days.

    Other namespaces aren't purged — they're abstract patterns and stay until
    decay archives them. Interactions are noisy source events; they hang around
    only long enough to feed the weekly LLM reflection (Phase C.4).
    """
    target_ns = _ns(MemoryNamespace.interactions)
    cutoff = _now() - timedelta(days=ttl_days)
    removed = 0
    for mid in list(memory_store.keys()):
        mem = memory_store[mid]
        if mem.namespace != target_ns:
            continue
        if mem.created_at < cutoff:
            del memory_store[mid]
            removed += 1
    if removed:
        save_memory_store()
    return removed
