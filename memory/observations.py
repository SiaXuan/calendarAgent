"""
Observation → Memory promotion engine (Phase C.3).

The N-gate is the most important safeguard from the plan:

    "用户某天心情不好拒绝 5 个 block 就被记成'讨厌 deep work'"

We avoid that by requiring N independent observations (default 3) within a
sliding window (default 7 days) for the same (action, task_kind, hour_bucket)
tuple before promoting to memory. Already-existing matching memories get
reinforced instead of duplicated.

Observations themselves are in-memory only — the persisted record is the
*promoted memory*, not the raw signal stream.
"""
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from memory import store as mem
from models.memory import (
    ActionType, HourBucket, Memory, MemoryNamespace, Observation,
    hour_to_bucket,
)
from storage import memory_store, observation_log

# Tuning knobs — single source of truth so tests + endpoint reference these.
PROMOTE_THRESHOLD = 3            # how many same-direction signals trigger promotion
PROMOTE_WINDOW_DAYS = 7          # rolling window the count is measured over
OBSERVATION_TTL_DAYS = 30        # drop ancient observations to keep memory low

# Confidence curve once promoted (caps at 0.9 — leave room for explicit "verified").
_BASE_CONFIDENCE = 0.4
_PER_OBS_BOOST = 0.15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pattern_key(action: ActionType, task_kind: str | None, hour_bucket: HourBucket) -> str:
    """Stable identifier for the (action, task_kind, hour_bucket) tuple."""
    return f"{action}|{task_kind or '*'}|{hour_bucket}"


def _find_existing_memory(pattern_key: str) -> Memory | None:
    """Look up a previously-promoted memory by its `structured.pattern_key`."""
    for m in memory_store.values():
        if (
            m.namespace[1] == MemoryNamespace.schedule_prefs.value
            and isinstance(m.structured, dict)
            and m.structured.get("pattern_key") == pattern_key
        ):
            return m
    return None


def _make_content(action: ActionType, task_kind: str | None, hour_bucket: HourBucket, count: int) -> str:
    """Human-readable phrasing for the Inspector + agent prompt."""
    verb = "accepts" if action == "accept" else "typically rejects"
    kind = task_kind or "any"
    return f"User {verb} {kind} work in the {hour_bucket} ({count} observations)"


def record(
    action: ActionType,
    block_key: str,
    *,
    hour: int,
    task_kind: str | None = None,
    cognitive_load: str | None = None,
) -> Observation:
    """
    Append an observation. Returns the saved record.

    Callers (the /memory/feedback endpoint) should then call promote() to check
    whether the new signal pushes any pattern over the threshold.
    """
    obs = Observation(
        id=str(uuid.uuid4()),
        timestamp=_now(),
        action=action,
        hour_bucket=hour_to_bucket(hour),
        task_kind=task_kind,
        cognitive_load=cognitive_load,
        block_key=block_key,
    )
    observation_log.append(obs)
    _gc_old_observations()
    return obs


def _gc_old_observations() -> None:
    """Drop observations older than OBSERVATION_TTL_DAYS to bound memory use."""
    cutoff = _now() - timedelta(days=OBSERVATION_TTL_DAYS)
    if observation_log and observation_log[0].timestamp < cutoff:
        # Mutate the same list object (storage exports the same reference)
        kept = [o for o in observation_log if o.timestamp >= cutoff]
        observation_log[:] = kept


def promote() -> list[Memory]:
    """
    Scan recent observations; for any (action, task_kind, hour_bucket) tuple
    that has ≥ PROMOTE_THRESHOLD signals in the last PROMOTE_WINDOW_DAYS, write
    or reinforce the corresponding memory.

    Returns the list of memories that were created or reinforced this round.
    """
    cutoff = _now() - timedelta(days=PROMOTE_WINDOW_DAYS)
    recent = [o for o in observation_log if o.timestamp >= cutoff]

    counter: Counter[str] = Counter()
    sources: dict[str, list[str]] = {}
    for o in recent:
        key = _pattern_key(o.action, o.task_kind, o.hour_bucket)
        counter[key] += 1
        sources.setdefault(key, []).append(o.id)

    touched: list[Memory] = []
    for pattern_key, count in counter.items():
        if count < PROMOTE_THRESHOLD:
            continue
        action_str, task_kind_str, bucket = pattern_key.split("|")
        task_kind: str | None = None if task_kind_str == "*" else task_kind_str
        action: ActionType = "accept" if action_str == "accept" else "dismiss"
        bucket_val: HourBucket = bucket   # type: ignore[assignment]

        target_conf = min(0.9, _BASE_CONFIDENCE + _PER_OBS_BOOST * count)
        existing = _find_existing_memory(pattern_key)
        if existing is None:
            new_mem = mem.add(
                MemoryNamespace.schedule_prefs,
                content=_make_content(action, task_kind, bucket_val, count),
                confidence=target_conf,
                structured={
                    "pattern_key": pattern_key,
                    "action": action,
                    "task_kind": task_kind,
                    "hour_bucket": bucket_val,
                    "observation_count": count,
                },
                source_event_ids=sources[pattern_key],
            )
            touched.append(new_mem)
        else:
            # Reinforce: bump confidence, refresh content/structured, update sources.
            updated = existing.model_copy(update={
                "confidence": max(existing.confidence, target_conf),
                "content": _make_content(action, task_kind, bucket_val, count),
                "structured": {
                    **(existing.structured or {}),
                    "observation_count": count,
                },
                "source_event_ids": sources[pattern_key],
                "last_reinforced_at": _now(),
            })
            memory_store[updated.id] = updated
            touched.append(updated)
    if touched:
        from storage import save_memory_store
        save_memory_store()
    return touched
