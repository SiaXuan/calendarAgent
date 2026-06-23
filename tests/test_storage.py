"""
Tests for storage.py — JSON persistence of health and task stores.

We monkey-patch the file paths to a tmp_path dir so real data/ files aren't
touched.
"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest

import storage
from models.health import HealthSnapshot, SleepData
from models.task import CognitiveLoad, Priority, Task, TaskKind


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Redirect storage's JSON files to a tmp dir + reset in-memory stores."""
    monkeypatch.setattr(storage, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "_HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(storage, "_TASKS_FILE", tmp_path / "tasks.json")
    storage.health_store.clear()
    storage.task_store.clear()
    yield tmp_path
    storage.health_store.clear()
    storage.task_store.clear()


def test_save_and_load_health_store_roundtrip(isolated_storage):
    """A saved snapshot loads back identical."""
    d = date(2026, 5, 15)
    snap = HealthSnapshot(
        date=d,
        sleep=SleepData(
            duration_hours=7.5,
            sleep_start=datetime(2026, 5, 14, 23, 0),
            sleep_end=datetime(2026, 5, 15, 6, 30),
        ),
        resting_heart_rate=58,
        hrv=45.0,
    )
    storage.health_store[d] = snap
    storage.save_health_store()

    # Wipe + load
    storage.health_store.clear()
    storage.load_health_store()

    assert d in storage.health_store
    loaded = storage.health_store[d]
    assert loaded.sleep.duration_hours == 7.5
    assert loaded.resting_heart_rate == 58


def test_save_and_load_task_store_roundtrip(isolated_storage):
    """A saved task loads back with all fields preserved, including task_kind."""
    task = Task(
        id="task_x",
        title="Test task",
        priority=Priority.high,
        cognitive_load=CognitiveLoad.deep,
        task_kind=TaskKind.insight,   # non-default value to prove persistence
        estimated_hours=1.5,
        deadline=date(2026, 5, 20),
        source="manual",
    )
    storage.task_store[task.id] = task
    storage.save_task_store()

    storage.task_store.clear()
    storage.load_task_store()

    assert "task_x" in storage.task_store
    loaded = storage.task_store["task_x"]
    assert loaded.title == "Test task"
    assert loaded.task_kind == TaskKind.insight
    assert loaded.priority == Priority.high


def test_schedule_store_roundtrip_survives_restart(isolated_storage, monkeypatch):
    """
    Regression: schedule_store wasn't persisted, so a backend restart dropped
    all manual adjustments (chat moves, drag pins). Now it round-trips.
    """
    monkeypatch.setattr(storage, "_SCHEDULE_FILE", isolated_storage / "schedule_store.json")
    from datetime import datetime
    from models.schedule import BlockType, DaySchedule, TimeBlock

    d = date(2026, 6, 15)
    blk = TimeBlock(
        start=datetime(2026, 6, 15, 14, 0), end=datetime(2026, 6, 15, 15, 0),
        block_type=BlockType.scheduled, task_id="t1", title="Moved to afternoon",
    )
    storage.schedule_store[d] = DaySchedule(
        date=d, energy_curve=[0.5] * 24, blocks=[blk], unscheduled=[], health_summary="ok",
    )
    storage.bump_schedule_version(d)   # triggers save
    saved_version = storage.current_version(d)

    # Simulate restart: wipe memory, reload from disk
    storage.schedule_store.clear()
    storage.schedule_version.clear()
    storage.load_schedule_store()

    assert d in storage.schedule_store
    restored = storage.schedule_store[d]
    assert restored.blocks[0].title == "Moved to afternoon"
    assert restored.blocks[0].start == datetime(2026, 6, 15, 14, 0)
    assert storage.current_version(d) == saved_version


def test_load_with_missing_file_is_noop(isolated_storage):
    """No file on disk → load is a silent no-op (doesn't raise)."""
    assert not (isolated_storage / "health.json").exists()
    storage.load_health_store()
    storage.load_task_store()
    assert storage.health_store == {}
    assert storage.task_store == {}


def test_load_corrupted_file_does_not_crash(isolated_storage):
    """Corrupted JSON is logged + skipped, not raised."""
    (isolated_storage / "health.json").write_text("not valid json {{{")
    (isolated_storage / "tasks.json").write_text("not valid json {{{")
    # Must not raise
    storage.load_health_store()
    storage.load_task_store()
    assert storage.health_store == {}
    assert storage.task_store == {}


def test_old_task_without_task_kind_loads_with_default(isolated_storage):
    """
    Backwards compat: tasks saved before task_kind was added must still load.
    Pydantic defaults to TaskKind.analytical.
    """
    (isolated_storage / "tasks.json").write_text(json.dumps({
        "task_legacy": {
            "id": "task_legacy",
            "title": "Old task without task_kind",
            "priority": "medium",
            "cognitive_load": "medium",
            "estimated_hours": 1.0,
            "source": "manual",
        }
    }))
    storage.load_task_store()
    assert "task_legacy" in storage.task_store
    assert storage.task_store["task_legacy"].task_kind == TaskKind.analytical
