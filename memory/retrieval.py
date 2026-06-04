"""
Memory retrieval helpers (Phase C.3 — pre-embedding).

These return a list of plain content strings ready to be injected as bullets in
an LLM system prompt. We deliberately avoid vector search here:

  * No OpenAI key required.
  * Deterministic — the same memories produce the same bullets every call.
  * The retrieval set is small (4 namespace buckets × maybe 10–30 items each),
    so a confidence floor + namespace filter beats embeddings on latency.

Phase C.3+ may add a VectorRetriever class that swaps in here without changing
callers.
"""
from memory import store as mem
from models.memory import Memory, MemoryNamespace

# Memories with confidence below this never reach an agent prompt.
# Inspector UI still shows them so users can see/fix the weak signals.
PROD_CONFIDENCE_FLOOR = 0.6


def for_task_ranking(*, max_items: int = 8) -> list[str]:
    """
    Memories injected into task_agent's system prompt when ranking subtasks.

    Currently returns content strings from `schedule_prefs` above the prod
    confidence floor, most-recently-reinforced first.
    """
    items = mem.list_by_namespace(
        MemoryNamespace.schedule_prefs,
        min_confidence=PROD_CONFIDENCE_FLOOR,
    )
    return [m.content for m in items[:max_items]]


def for_chat(*, max_items: int = 6) -> list[str]:
    """Memories injected into chat_agent's system prompt for adjustments."""
    items: list[Memory] = []
    for ns in (MemoryNamespace.schedule_prefs, MemoryNamespace.task_lexicon):
        items.extend(mem.list_by_namespace(
            ns, min_confidence=PROD_CONFIDENCE_FLOOR,
        ))
    # Sort by most recent reinforcement so the freshest signals lead.
    items.sort(key=lambda m: m.last_reinforced_at, reverse=True)
    return [m.content for m in items[:max_items]]
