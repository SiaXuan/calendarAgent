"""
Memory Inspector API (Phase C.2).

The plan calls out: "Inspector UI must come before write logic." Reason: once
rule-triggered writes (C.3) and LLM reflection (C.4) start firing, the user
needs a way to see what got memorized and correct it. Without the Inspector,
bad memories silently poison every subsequent prompt and the user has no
escape hatch.

Endpoints:
  GET    /memory                    list all, optional ?namespace= + ?min_confidence=
  POST   /memory                    manual create (testing + Inspector "add" button)
  PATCH  /memory/{id}               edit content/confidence/user_verified
  DELETE /memory/{id}               remove
  POST   /memory/decay              admin: run a decay sweep on demand
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from memory import observations as obs
from memory import store as mem
from models.memory import (
    ActionType, Memory, MemoryNamespace, MemoryUpdate, Observation,
)


router = APIRouter()


class MemoryCreate(BaseModel):
    """Inspector "add new memory" form. ID + timestamps are server-generated."""
    namespace: MemoryNamespace
    content: str = Field(min_length=1)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    structured: dict | None = None
    user_verified: bool = True   # manual entries default to verified


class DecayRunResult(BaseModel):
    decayed: int
    archived: int


@router.get("/memory", response_model=list[Memory])
async def list_memories(
    namespace: MemoryNamespace | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    include_unverified: bool = Query(default=True),
):
    """List memories, most-recently-reinforced first."""
    return mem.list_by_namespace(
        namespace,
        min_confidence=min_confidence,
        include_unverified=include_unverified,
    )


@router.get("/memory/{memory_id}", response_model=Memory)
async def get_memory(memory_id: str):
    m = mem.get(memory_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return m


@router.post("/memory", response_model=Memory)
async def create_memory(payload: MemoryCreate):
    """Manual create — used by the Inspector + tests. Auto-write path comes in C.3."""
    return mem.add(
        payload.namespace,
        content=payload.content,
        confidence=payload.confidence,
        structured=payload.structured,
        user_verified=payload.user_verified,
    )


@router.patch("/memory/{memory_id}", response_model=Memory)
async def update_memory(memory_id: str, patch: MemoryUpdate):
    updated = mem.update(memory_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return updated


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    if not mem.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"deleted": memory_id}


class FeedbackRequest(BaseModel):
    """User accept/dismiss signal from the timeline."""
    action: ActionType
    block_key: str
    hour: int = Field(ge=0, le=23)
    task_kind: str | None = None
    cognitive_load: str | None = None


class FeedbackResponse(BaseModel):
    observation: Observation
    promoted: list[Memory]


@router.post("/memory/feedback", response_model=FeedbackResponse)
async def submit_feedback(payload: FeedbackRequest):
    """
    Record a user accept/dismiss and check whether it pushes any pattern over
    the N-threshold. New / reinforced memories are returned so the frontend can
    optionally toast "Learned: ...".
    """
    observation = obs.record(
        payload.action,
        payload.block_key,
        hour=payload.hour,
        task_kind=payload.task_kind,
        cognitive_load=payload.cognitive_load,
    )
    promoted = obs.promote()
    return FeedbackResponse(observation=observation, promoted=promoted)


@router.post("/memory/decay", response_model=DecayRunResult)
async def run_decay(weeks_elapsed: float = Query(default=1.0, gt=0.0)):
    """
    Admin endpoint to trigger a decay sweep on demand. The scheduled job in
    Phase C.4 will hit this same path on a cron.
    """
    result = mem.decay_pass(weeks_elapsed=weeks_elapsed)
    return DecayRunResult(**result)
