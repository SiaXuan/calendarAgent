"""
Persistence layer — JSON-backed in-memory stores for Phase 1-3.

These were originally living in agents/orchestrator.py. Phase A migration
splits them out so the orchestrator (and its eventual LangGraph replacement)
can be deleted without dragging persistence with it.

All four stores are loaded on backend startup via lifespan hooks in main.py
and saved opportunistically by the API routes that mutate them.

Phase C will add a fifth store (LangMem memories); we'll add it here.
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from models.health import HealthSnapshot
from models.memory import Memory
from models.project import CompletionRecord, PlanSnapshotItem, Project
from models.schedule import DaySchedule
from models.task import Subtask, Task


class PinSpec(BaseModel):
    """A user-pinned subtask position (set by drag-to-move or pomodoro +/-).

    During schedule_graph the pin gets converted to a TimeBlock and treated as
    an additional fixed_block — the rest of the scheduler then routes around
    it. Pins are per-date because they describe a specific calendar slot.
    """
    start: datetime
    duration_min: int


_log = logging.getLogger("dayflow")
_DATA_DIR = Path(__file__).parent / "data"
_HEALTH_FILE = _DATA_DIR / "health_store.json"
_TASKS_FILE = _DATA_DIR / "task_store.json"
_MEMORY_FILE = _DATA_DIR / "memory_store.json"
_SCHEDULE_FILE = _DATA_DIR / "schedule_store.json"
_PROJECT_FILE = _DATA_DIR / "project_store.json"
_COMPLETION_FILE = _DATA_DIR / "completion_store.json"
_PROJECT_PLAN_FILE = _DATA_DIR / "project_plan_store.json"
_PROJECT_TASK_FILE = _DATA_DIR / "project_task_store.json"


# ─── In-memory stores ────────────────────────────────────────────────────────

# Snapshots keyed by date (one per day). Persisted to JSON.
health_store: dict[date, HealthSnapshot] = {}

# Tasks keyed by task_id. Persisted to JSON. This is the DAILY SCHEDULING pool:
# the schedule graph reads it wholesale, so it must contain ONLY tasks meant to
# be scheduled now (ad-hoc + reminder-synced). Unconfirmed project nodes live in
# project_task_store, not here.
task_store: dict[str, Task] = {}

# Project-scoped tasks (Phase 4). Keyed by task_id. A project's plan nodes,
# AWAITING confirmation — kept OUT of the global task_store so the daily
# scheduler never auto-schedules unconfirmed project work. The project layer
# (snapshot / replan / write-to-calendar) reads from here; the multi-day planner
# (Step 1.6) will be what promotes these into the daily schedule. Persisted.
project_task_store: dict[str, Task] = {}

# Generated DaySchedule per date. PERSISTED — so manual adjustments (chat
# agent moves, drag pins, accepted proposals) survive a backend restart instead
# of being regenerated from scratch (which would drop all of them).
schedule_store: dict[date, DaySchedule] = {}

# Monotonic version per date — bumped on every schedule mutation. Used by the
# conversational agent for optimistic-concurrency on stale Proposals (S3).
schedule_version: dict[date, int] = {}


def current_version(d: date) -> int:
    return schedule_version.get(d, 0)


def bump_schedule_version(d: date) -> int:
    schedule_version[d] = schedule_version.get(d, 0) + 1
    # Persist the schedule on every mutation so a restart restores adjustments.
    save_schedule_store()
    return schedule_version[d]

# Confirmed subtask plans from the per-task planning chat — when present,
# they override the LLM's task decomposition. Cleared on regenerate.
subtask_overrides: dict[str, list[Subtask]] = {}

# User-pinned subtask positions, keyed by date then by block_key
# (block_key = "{task_id}::{title}" — matches the frontend's `blockKey`).
# In-memory only — pinning is intentionally ephemeral; full regenerate clears.
subtask_pins: dict[date, dict[str, PinSpec]] = {}

# Projects (Phase 4). Keyed by project.id. Persisted to JSON.
project_store: dict[str, Project] = {}

# Completion records (Phase 4). Keyed by block_key ("{parent_id}::{title}").
# The source of truth for "was this block actually done" — Apple Calendar has
# no done flag. Feeds the heatmap, review, and completion-aware replan.
completion_store: dict[str, CompletionRecord] = {}

# Last-written decomposition per project (Phase 4). Keyed by project_id.
# Used by replan to diff changed vs unchanged blocks. Persisted to JSON.
project_plan_store: dict[str, list[PlanSnapshotItem]] = {}

# Long-term memories (Phase C). Keyed by memory.id. Persisted to JSON.
# Namespace lookups + retrieval helpers live in memory/store.py.
memory_store: dict[str, Memory] = {}

# Pre-memory observation log (Phase C.3). In-memory only — these are noisy
# signals that get promoted to memory_store once N same-direction events
# accumulate. Server restart wipes the counter (intentional — stale signals
# shouldn't promote weeks later).
from models.memory import Observation as _Obs   # avoid top-level circular import
observation_log: list[_Obs] = []

# Conversational-agent run logs (S3 observability). In-memory; for replay+eval.
agent_run_log: list[dict] = []

# Pending major-change Proposals awaiting user confirm, keyed by date.
# Each: {proposal_id, base_version, staged_blocks, summary, created_at_iso}.
pending_proposals: dict[date, dict] = {}

# Conversational-agent message history per date (S3 multi-turn context).
# [{role: "user"|"assistant", content: str}] — lets follow-ups ("我是说…")
# build on prior turns. In-memory; cleared on full regenerate.
chat_sessions: dict[date, list[dict]] = {}


# ─── health_store persistence ────────────────────────────────────────────────

def save_health_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {
            str(d): snapshot.model_dump(mode="json")
            for d, snapshot in health_store.items()
        }
        _HEALTH_FILE.write_text(json.dumps(payload, default=str))
    except Exception as exc:
        _log.warning("Could not save health store: %s", exc)


def load_health_store() -> None:
    if not _HEALTH_FILE.exists():
        return
    try:
        payload = json.loads(_HEALTH_FILE.read_text())
        for date_str, data in payload.items():
            d = date.fromisoformat(date_str)
            health_store[d] = HealthSnapshot.model_validate(data)
        _log.info("Loaded %d health snapshot(s) from disk.", len(health_store))
    except Exception as exc:
        _log.warning("Could not load health store: %s", exc)


# ─── task_store persistence ──────────────────────────────────────────────────

def save_task_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {tid: t.model_dump(mode="json") for tid, t in task_store.items()}
        _TASKS_FILE.write_text(json.dumps(payload, default=str))
    except Exception as exc:
        _log.warning("Could not save task store: %s", exc)


def load_task_store() -> None:
    if not _TASKS_FILE.exists():
        return
    try:
        payload = json.loads(_TASKS_FILE.read_text())
        for tid, data in payload.items():
            task_store[tid] = Task.model_validate(data)
        _log.info("Loaded %d task(s) from disk.", len(task_store))
    except Exception as exc:
        _log.warning("Could not load task store: %s", exc)


# ─── project_task_store persistence (Phase 4) ────────────────────────────────

def save_project_task_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {tid: t.model_dump(mode="json") for tid, t in project_task_store.items()}
        _PROJECT_TASK_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save project task store: %s", exc)


def load_project_task_store() -> None:
    if _PROJECT_TASK_FILE.exists():
        try:
            payload = json.loads(_PROJECT_TASK_FILE.read_text())
            for tid, data in payload.items():
                project_task_store[tid] = Task.model_validate(data)
            _log.info("Loaded %d project task(s) from disk.", len(project_task_store))
        except Exception as exc:
            _log.warning("Could not load project task store: %s", exc)
    _migrate_project_tasks_out_of_task_store()


def _migrate_project_tasks_out_of_task_store() -> None:
    """One-time: pull any project-owned tasks out of the global scheduling
    task_store into the project-scoped store, so unconfirmed project nodes stop
    being auto-scheduled on the daily path. Idempotent — safe to run every boot.
    Load task_store BEFORE calling this."""
    moved = 0
    for tid in list(task_store.keys()):
        t = task_store[tid]
        if t.project_id is not None:
            project_task_store.setdefault(tid, t)
            del task_store[tid]
            moved += 1
    if moved:
        save_task_store()
        save_project_task_store()
        _log.info("Migrated %d project task(s) out of the scheduling store.", moved)


# ─── memory_store persistence ────────────────────────────────────────────────

def save_memory_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {mid: m.model_dump(mode="json") for mid, m in memory_store.items()}
        _MEMORY_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save memory store: %s", exc)


def load_memory_store() -> None:
    if not _MEMORY_FILE.exists():
        return
    try:
        payload = json.loads(_MEMORY_FILE.read_text())
        for mid, data in payload.items():
            memory_store[mid] = Memory.model_validate(data)
        _log.info("Loaded %d memory record(s) from disk.", len(memory_store))
    except Exception as exc:
        _log.warning("Could not load memory store: %s", exc)


# ─── project layer persistence (Phase 4) ─────────────────────────────────────

def save_project_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {pid: p.model_dump(mode="json") for pid, p in project_store.items()}
        _PROJECT_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save project store: %s", exc)


def load_project_store() -> None:
    if not _PROJECT_FILE.exists():
        return
    try:
        payload = json.loads(_PROJECT_FILE.read_text())
        for pid, data in payload.items():
            project_store[pid] = Project.model_validate(data)
        _log.info("Loaded %d project(s) from disk.", len(project_store))
    except Exception as exc:
        _log.warning("Could not load project store: %s", exc)


def save_completion_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {k: r.model_dump(mode="json") for k, r in completion_store.items()}
        _COMPLETION_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save completion store: %s", exc)


def load_completion_store() -> None:
    if not _COMPLETION_FILE.exists():
        return
    try:
        payload = json.loads(_COMPLETION_FILE.read_text())
        for k, data in payload.items():
            completion_store[k] = CompletionRecord.model_validate(data)
        _log.info("Loaded %d completion record(s) from disk.", len(completion_store))
    except Exception as exc:
        _log.warning("Could not load completion store: %s", exc)


def save_project_plan_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {
            pid: [item.model_dump(mode="json") for item in items]
            for pid, items in project_plan_store.items()
        }
        _PROJECT_PLAN_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save project plan store: %s", exc)


def load_project_plan_store() -> None:
    if not _PROJECT_PLAN_FILE.exists():
        return
    try:
        payload = json.loads(_PROJECT_PLAN_FILE.read_text())
        for pid, items in payload.items():
            project_plan_store[pid] = [PlanSnapshotItem.model_validate(i) for i in items]
        _log.info("Loaded plan snapshots for %d project(s) from disk.", len(project_plan_store))
    except Exception as exc:
        _log.warning("Could not load project plan store: %s", exc)


# ─── schedule_store persistence (survives restart → keeps manual edits) ───────

def save_schedule_store() -> None:
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        payload = {
            str(d): {
                "schedule": sch.model_dump(mode="json"),
                "version": schedule_version.get(d, 0),
                "pins": {
                    k: p.model_dump(mode="json")
                    for k, p in subtask_pins.get(d, {}).items()
                },
            }
            for d, sch in schedule_store.items()
        }
        _SCHEDULE_FILE.write_text(json.dumps(payload, default=str, ensure_ascii=False))
    except Exception as exc:
        _log.warning("Could not save schedule store: %s", exc)


def load_schedule_store() -> None:
    if not _SCHEDULE_FILE.exists():
        return
    try:
        payload = json.loads(_SCHEDULE_FILE.read_text())
        for date_str, entry in payload.items():
            d = date.fromisoformat(date_str)
            schedule_store[d] = DaySchedule.model_validate(entry["schedule"])
            schedule_version[d] = entry.get("version", 0)
            pins = entry.get("pins") or {}
            if pins:
                subtask_pins[d] = {k: PinSpec.model_validate(v) for k, v in pins.items()}
        _log.info("Loaded %d cached schedule(s) from disk.", len(schedule_store))
    except Exception as exc:
        _log.warning("Could not load schedule store: %s", exc)
