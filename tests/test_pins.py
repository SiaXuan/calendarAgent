"""
Tests for the pin abstraction (drag-to-move + pomodoro +/-).

Coverage:
- find_nearest_slot conflict resolution
- apply_pins_node converts pins → fixed blocks and removes the subtask
- POST/DELETE /schedule/{date}/pin endpoints
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agents import nodes
from agents.scheduler_agent import find_nearest_slot
from agents.task_agent import _LLMSubtask, _LLMSubtaskList
from main import app
from models.schedule import BlockType, TimeBlock
from models.task import CognitiveLoad, TaskKind
from models.user import Language
from storage import PinSpec, subtask_pins


# ─── find_nearest_slot ──────────────────────────────────────────────────────

class TestFindNearestSlot:
    def setup_method(self):
        self.d = date(2026, 5, 15)
        # Fixed lunch meeting 12:00–13:00
        self.lunch = TimeBlock(
            start=datetime(2026, 5, 15, 12, 0),
            end=datetime(2026, 5, 15, 13, 0),
            block_type=BlockType.fixed,
            title="Lunch meeting",
        )

    def test_preferred_slot_clear_returns_it(self):
        """When the requested time has no conflict, return it (snapped to 15min)."""
        result = find_nearest_slot(
            datetime(2026, 5, 15, 10, 0), 60, [self.lunch], 8, 22,
        )
        assert result == datetime(2026, 5, 15, 10, 0)

    def test_conflict_pushes_forward(self):
        """Dropping at 12:30 (inside lunch) should snap to after lunch + buffer."""
        result = find_nearest_slot(
            datetime(2026, 5, 15, 12, 30), 30, [self.lunch], 8, 22,
        )
        # Lunch ends 13:00, so the slot at 13:00 is the first one that's clear
        # (find_nearest_slot probes forward in 15min steps from snapped 12:30).
        assert result >= datetime(2026, 5, 15, 13, 0)

    def test_snaps_to_15_min(self):
        """Non-aligned input gets snapped to nearest 15-min."""
        result = find_nearest_slot(
            datetime(2026, 5, 15, 10, 23), 30, [], 8, 22,
        )
        # 10:23 → snapped to 10:15
        assert result == datetime(2026, 5, 15, 10, 15)

    def test_respects_work_end(self):
        """A pin that would extend past work_end gets pushed back, not out of bounds."""
        result = find_nearest_slot(
            datetime(2026, 5, 15, 21, 30), 60, [], 8, 22,
        )
        # 21:30 + 60min = 22:30, exceeds work_end=22 → must shift earlier
        assert result + timedelta(minutes=60) <= datetime(2026, 5, 15, 22, 0)


# ─── apply_pins_node ────────────────────────────────────────────────────────

def _subtask(parent_id: str, title: str, minutes: int = 60):
    from models.task import Subtask
    return Subtask(
        parent_id=parent_id, title=title,
        cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
        estimated_minutes=minutes, suggested_date=date(2026, 5, 15),
    )


def test_apply_pins_no_pins_is_noop(clean_stores):
    state = {
        "target_date": date(2026, 5, 15),
        "language": Language.en,
        "subtasks": [_subtask("t1", "Work")],
        "fixed_blocks": [],
        "free_windows": [],
    }
    patch = nodes.apply_pins_node(state)
    assert patch == {}


def test_apply_pins_converts_pinned_subtask_to_fixed_block(clean_stores):
    """A pinned subtask becomes a fixed_block; remaining subtasks shrink."""
    d = date(2026, 5, 15)
    s1 = _subtask("t1", "Pinned work", 60)
    s2 = _subtask("t2", "Other work", 60)
    subtask_pins[d] = {
        "t1::Pinned work": PinSpec(
            start=datetime(2026, 5, 15, 15, 0),
            duration_min=90,
        )
    }
    state = {
        "target_date": d,
        "language": Language.en,
        "subtasks": [s1, s2],
        "fixed_blocks": [],
        "free_windows": [],
    }
    patch = nodes.apply_pins_node(state)
    # s1 is gone from subtasks
    assert len(patch["subtasks"]) == 1
    assert patch["subtasks"][0].parent_id == "t2"
    # A new fixed-style block was added at the pin location with the pinned duration
    assert len(patch["fixed_blocks"]) == 1
    pinned_block = patch["fixed_blocks"][0]
    assert pinned_block.start == datetime(2026, 5, 15, 15, 0)
    assert pinned_block.end == datetime(2026, 5, 15, 16, 30)
    assert pinned_block.task_id == "t1"
    assert pinned_block.title == "Pinned work"


def test_apply_pins_silently_drops_orphan_pins(clean_stores):
    """Pin referencing a deleted task → silently dropped, no crash."""
    d = date(2026, 5, 15)
    subtask_pins[d] = {
        "ghost::Vanished task": PinSpec(
            start=datetime(2026, 5, 15, 10, 0), duration_min=30,
        )
    }
    state = {
        "target_date": d,
        "language": Language.en,
        "subtasks": [_subtask("real", "Real work")],
        "fixed_blocks": [],
        "free_windows": [],
    }
    patch = nodes.apply_pins_node(state)
    assert len(patch["subtasks"]) == 1
    # No fixed_blocks added because no pins matched
    assert "fixed_blocks" not in patch


# ─── /schedule/{date}/pin endpoint ──────────────────────────────────────────

@pytest.fixture
def client(clean_stores):
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def seeded_schedule(client, mock_sonnet, mock_caldav, mock_reminders_sync, sample_task):
    """Generate a schedule first so the pin endpoints have something to mutate."""
    from storage import task_store
    task_store[sample_task.id] = sample_task
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id=sample_task.id, title="Energy curve work",
            estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))
    r = client.post("/schedule/generate", json={"date": "2026-05-15"})
    assert r.status_code == 200
    return sample_task.id


async def test_pin_endpoint_404_when_no_schedule(client):
    r = client.post(
        "/schedule/2026-05-15/pin",
        json={"block_key": "task_x::Work", "start_iso": "2026-05-15T15:00:00"},
    )
    assert r.status_code == 404


async def test_pin_endpoint_404_when_block_not_found(client, seeded_schedule):
    r = client.post(
        "/schedule/2026-05-15/pin",
        json={"block_key": "no-such-task::title", "start_iso": "2026-05-15T15:00:00"},
    )
    assert r.status_code == 404


async def test_pin_endpoint_moves_block_to_requested_start(client, seeded_schedule):
    """Pin a subtask to 3:00pm — it should appear there in the returned schedule."""
    task_id = seeded_schedule
    block_key = f"{task_id}::Energy curve work"
    r = client.post(
        f"/schedule/2026-05-15/pin",
        json={
            "block_key": block_key,
            "start_iso": "2026-05-15T15:00:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["adjusted"] is False
    assert body["start"].startswith("2026-05-15T15:00:00")
    # The new schedule should contain a block matching that pin
    pinned = next(
        (b for b in body["schedule"]["blocks"]
         if b.get("task_id") == task_id and b["title"] == "Energy curve work"),
        None,
    )
    assert pinned is not None
    assert pinned["start"].startswith("2026-05-15T15:00:00")


async def test_pin_endpoint_conflict_resolution(client, seeded_schedule):
    """
    Try to pin into the meal block — endpoint snaps to nearest free slot
    and reports adjusted=True.
    """
    task_id = seeded_schedule
    block_key = f"{task_id}::Energy curve work"
    # Lunch meeting fixture starts at 13:00 (see conftest.mock_caldav)
    # Try to pin at 13:30 (inside lunch)
    r = client.post(
        f"/schedule/2026-05-15/pin",
        json={
            "block_key": block_key,
            "start_iso": "2026-05-15T13:30:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["adjusted"] is True


async def test_unpin_endpoint_removes_pin(client, seeded_schedule):
    task_id = seeded_schedule
    block_key = f"{task_id}::Energy curve work"
    # Pin first
    client.post(
        f"/schedule/2026-05-15/pin",
        json={"block_key": block_key, "start_iso": "2026-05-15T15:00:00"},
    )
    # Verify storage has the pin
    assert date(2026, 5, 15) in subtask_pins
    # Now unpin
    r = client.delete(f"/schedule/2026-05-15/pin/{block_key}")
    assert r.status_code == 200
    # Pin storage emptied
    assert date(2026, 5, 15) not in subtask_pins


async def test_unpin_404_when_no_pin(client, seeded_schedule):
    r = client.delete("/schedule/2026-05-15/pin/nonexistent::title")
    assert r.status_code == 404


# ─── /schedule/{date}/blocks/{key}/remove endpoint ──────────────────────────

async def test_remove_block_takes_it_off_the_schedule(client, seeded_schedule):
    """Delete-the-card flow: the block is gone from the returned schedule."""
    task_id = seeded_schedule
    block_key = f"{task_id}::Energy curve work"
    r = client.post(f"/schedule/2026-05-15/blocks/{block_key}/remove")
    assert r.status_code == 200
    gone = all(
        not (b.get("task_id") == task_id and b["title"] == "Energy curve work")
        for b in r.json()["blocks"]
    )
    assert gone


async def test_remove_block_404_when_no_schedule(client):
    r = client.post("/schedule/2026-05-15/blocks/task_x::Work/remove")
    assert r.status_code == 404


async def test_remove_block_404_when_block_not_found(client, seeded_schedule):
    r = client.post("/schedule/2026-05-15/blocks/no-such::title/remove")
    assert r.status_code == 404


# ─── reflow_after_pin: bypasses LLM ────────────────────────────────────────

async def test_pin_endpoint_does_not_call_llm(client, seeded_schedule, mock_sonnet):
    """
    Regression: pin must use the lightweight reflow path that reuses cached
    state — NOT the full schedule_graph (which would call sonnet for ranking).
    This is what makes drag feel snappy in the UI.
    """
    task_id = seeded_schedule
    # Clear LLM call log AFTER seed has already used 1 call to rank tasks
    initial_llm_calls = len(mock_sonnet._structured.calls)

    r = client.post(
        f"/schedule/2026-05-15/pin",
        json={
            "block_key": f"{task_id}::Energy curve work",
            "start_iso": "2026-05-15T15:00:00",
        },
    )
    assert r.status_code == 200
    # Pin path must NOT invoke the LLM at all
    assert len(mock_sonnet._structured.calls) == initial_llm_calls, (
        "Pin endpoint called the LLM — reflow path is not being used"
    )


async def test_pin_falls_back_to_full_graph_when_no_schedule(
    clean_stores, mock_sonnet, mock_caldav, mock_reminders_sync,
):
    """
    If schedule_store has nothing for the date (e.g. server restarted),
    reflow_after_pin must fall back to the full graph rather than crash.
    """
    from graphs.schedule_graph import reflow_after_pin

    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[]))

    # No prior schedule for this date → fallback runs the full graph
    result = await reflow_after_pin(date(2026, 5, 15))
    assert result.date == date(2026, 5, 15)
    # Full graph ran (fetch_health set the source); no health seeded → empty curve
    assert result.energy_source == "none"
    assert result.energy_curve == []


async def test_reflow_preserves_other_blocks_no_resolve(clean_stores):
    """
    Regression: resizing/pinning ONE block must not relocate the others.
    Earlier reflow re-ran the greedy scheduler, so accepting an agent plan
    (deep work moved to afternoon) then tweaking one card's duration on Today
    snapped the rest back to the high-energy morning. Reflow now preserves
    positions and only cascades overlaps.
    """
    from datetime import datetime
    from graphs.schedule_graph import reflow_after_pin
    from models.schedule import BlockType, DaySchedule, TimeBlock
    from models.task import CognitiveLoad, TaskKind
    from storage import PinSpec, schedule_store, schedule_version, subtask_pins

    d = date(2026, 6, 15)
    curve = [0.2] * 24
    for h in range(8, 12):
        curve[h] = 0.9   # high-energy morning — where greedy would dump deep work

    def deep(task_id, title, h0, h1):
        return TimeBlock(
            start=datetime(2026, 6, 15, h0, 0), end=datetime(2026, 6, 15, h1, 0),
            block_type=BlockType.scheduled, task_id=task_id, title=title,
            cognitive_load=CognitiveLoad.deep, task_kind=TaskKind.analytical,
        )

    # Two deep blocks the agent placed in the AFTERNOON.
    a = deep("t1", "A", 14, 15)
    b = deep("t2", "B", 16, 17)
    schedule_store[d] = DaySchedule(
        date=d, energy_curve=curve, blocks=[a, b], unscheduled=[], health_summary="",
    )
    schedule_version[d] = 1
    # Pomodoro-resize A: keep its 14:00 start, grow to 90min.
    subtask_pins[d] = {"t1::A": PinSpec(start=datetime(2026, 6, 15, 14, 0), duration_min=90)}

    new = await reflow_after_pin(d)
    by = {blk.title: blk for blk in new.blocks}
    # A resized in place
    assert by["A"].start == datetime(2026, 6, 15, 14, 0)
    assert int((by["A"].end - by["A"].start).total_seconds() // 60) == 90
    # B did NOT jump back to the morning — still in the afternoon
    assert by["B"].start.hour >= 14


async def test_reflow_preserves_pinned_block_position(
    client, seeded_schedule,
):
    """
    Lightweight reflow should honor the pin: the pinned subtask appears at
    the requested time in the new schedule, just like the full graph would.
    """
    task_id = seeded_schedule
    block_key = f"{task_id}::Energy curve work"
    r = client.post(
        f"/schedule/2026-05-15/pin",
        json={
            "block_key": block_key,
            "start_iso": "2026-05-15T15:00:00",
        },
    )
    assert r.status_code == 200
    blocks = r.json()["schedule"]["blocks"]
    pinned = next(
        (b for b in blocks
         if b.get("task_id") == task_id and b["title"] == "Energy curve work"),
        None,
    )
    assert pinned is not None
    assert pinned["start"].startswith("2026-05-15T15:00:00")
