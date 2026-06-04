"""
Memory model for Phase C — long-term user-habit learning.

Design notes (from docs/phase3-plan.md C.1):
- Memories are *abstract patterns*, not raw events.
  Example: "user prefers analytical work in mornings" — not "rejected block at
  9am on Tuesday".
- Per-memory `confidence` lets us combine many weak signals into one strong rule
  without binary thresholds.
- `decay_rate` + `last_reinforced_at` enable the weekly decay sweep that
  prevents stale patterns from sticking around forever.
- `source_event_ids` keep an audit trail so the Memory Inspector UI can show
  the user WHY each memory exists.

Namespaces are tuples to support nested future expansion (e.g. multi-user as
`(user_id, schedule_prefs)`). For Phase C we hard-code `"default"` as the
first element.
"""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MemoryNamespace(str, Enum):
    """The four buckets the agent learns into. See docs/phase3-plan.md C.1.

    The string value is the 2nd tuple element; `("default", value)` is the full
    namespace passed to the LangGraph store.
    """

    schedule_prefs = "schedule_prefs"
    """Time-of-day + task-kind preferences. Feeds task_agent + chat_agent."""

    task_lexicon = "task_lexicon"
    """Task-description → meaning. E.g. 'compile bibliography' → admin, ~30min.
    Feeds the future profile_graph (Phase D)."""

    physiological = "physiological"
    """Personal baselines (HRV, RHR, sleep duration). Feeds health rules (Phase E)."""

    interactions = "interactions"
    """Episodic log with 30-day TTL. Source events for higher-level memories."""


class Memory(BaseModel):
    """A single learned pattern.

    Always retrieve via `confidence > 0.6` filter in prod paths so weak signals
    don't pollute prompts. The Inspector UI shows everything regardless.
    """

    id: str
    namespace: tuple[str, str]      # ("default", MemoryNamespace.value)
    content: str                    # natural-language fact
    structured: dict | None = None  # optional machine-readable form
    confidence: float = Field(ge=0.0, le=1.0)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    last_reinforced_at: datetime
    decay_rate: float = 0.05        # confidence -= this per week without reinforcement
    user_verified: bool = False     # the user confirmed it in the Inspector


class MemoryUpdate(BaseModel):
    """Patch payload from the Memory Inspector — every field optional."""

    content: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    user_verified: bool | None = None


# ─── Observation (Phase C.3 — pre-memory signal) ────────────────────────────

ActionType = Literal["accept", "dismiss"]

# 5 hour buckets matching common chronotype regions (Pink "When" 2018).
HourBucket = Literal["morning", "midday", "afternoon", "evening", "night"]


def hour_to_bucket(hour: int) -> HourBucket:
    """5–11 → morning, 11–14 → midday, 14–17 → afternoon, 17–21 → evening, else night."""
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "midday"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


class Observation(BaseModel):
    """
    A single piece of evidence — user accepted or dismissed a scheduled block.

    Observations live in-memory and feed the promotion engine (memory/observations.py):
    once N same-direction observations accumulate for the same (action, task_kind,
    hour_bucket) tuple inside a sliding window, they promote into a Memory.

    Single observations are intentionally NOT memories — the plan calls this out
    as the most important safeguard against "bad mood → noisy memory" (C.4).
    """

    id: str
    timestamp: datetime
    action: ActionType
    hour_bucket: HourBucket
    task_kind: str | None = None         # TaskKind value (analytical / insight / admin)
    cognitive_load: str | None = None    # CognitiveLoad value (deep / medium / light)
    block_key: str                       # "{task_id}::{title}" for source attribution
