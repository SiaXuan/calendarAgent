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
from datetime import date
from pathlib import Path

from models.health import HealthSnapshot
from models.schedule import DaySchedule
from models.task import Subtask, Task


_log = logging.getLogger("dayflow")
_DATA_DIR = Path(__file__).parent / "data"
_HEALTH_FILE = _DATA_DIR / "health_store.json"
_TASKS_FILE = _DATA_DIR / "task_store.json"


# ─── In-memory stores ────────────────────────────────────────────────────────

# Snapshots keyed by date (one per day). Persisted to JSON.
health_store: dict[date, HealthSnapshot] = {}

# Tasks keyed by task_id. Persisted to JSON.
task_store: dict[str, Task] = {}

# Generated DaySchedule per date. NOT persisted (regenerated on demand).
schedule_store: dict[date, DaySchedule] = {}

# Confirmed subtask plans from the per-task planning chat — when present,
# they override the LLM's task decomposition. Cleared on regenerate.
subtask_overrides: dict[str, list[Subtask]] = {}


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
