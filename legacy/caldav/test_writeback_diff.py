"""
Step 0 hardening — block-level diff write-back.

Verifies the reconcile behaviour of write_schedule_to_calendar without touching
real iCloud: CalDAV I/O is monkeypatched. The critical property is that a FAILED
delete must NOT be followed by a write (that's what used to create duplicates).
"""
from datetime import date, datetime

import pytest

import integrations.caldav_client as caldav
from agents import nodes
from agents.calendar_writeback import (
    _AGENT_TAG_PREFIX, _tag_key, block_key, write_schedule_to_calendar,
)
from integrations.caldav_client import CalDAVError
from models.schedule import BlockType, DaySchedule, TimeBlock
from storage import schedule_store

D = date(2026, 6, 15)


def _blk(task_id, title, hour):
    return TimeBlock(
        start=datetime(2026, 6, 15, hour, 0), end=datetime(2026, 6, 15, hour + 1, 0),
        block_type=BlockType.scheduled, task_id=task_id, title=title,
    )


def _tk(task_id, title):
    """The hashed tag key the write-back diff matches on."""
    return _tag_key(f"{task_id}::{title}")


def _existing(task_id, title, hour, event_title=None):
    """A fake fetched active event, keyed by the hashed tag key like fetch does."""
    return {
        "key": _tk(task_id, title),
        "title": event_title if event_title is not None else title,
        "start": datetime(2026, 6, 15, hour, 0), "end": datetime(2026, 6, 15, hour + 1, 0),
        "description": "", "tag_line": "",
    }


@pytest.fixture
def _clean_cache():
    nodes._calendar_cache.pop(D, None)
    yield
    nodes._calendar_cache.pop(D, None)
    schedule_store.pop(D, None)


def _install_schedule():
    schedule_store[D] = DaySchedule(
        date=D, energy_curve=[0.5] * 24,
        blocks=[
            _blk("a", "A", 9),   # unchanged (existing matches)
            _blk("b", "B", 10),  # changed  (existing title differs)
            _blk("c", "C", 11),  # added    (no existing)
        ],
        unscheduled=[], health_summary="OK",
    )


async def test_diff_leaves_unchanged_deletes_changed_and_removed(monkeypatch, _clean_cache):
    _install_schedule()
    monkeypatch.setattr(caldav, "fetch_agent_events", lambda d, prefix: [
        _existing("a", "A", 9),                          # matches block A → unchanged
        _existing("b", "B", 10, event_title="B-old"),    # title differs → changed
        _existing("d", "D", 14),                         # not desired → removed
    ])
    writes, deletes = [], []
    monkeypatch.setattr(caldav, "write_event",
                        lambda title, s, e, desc="", tag=caldav.AGENT_DESC_TAG: writes.append(title) or "uid")
    monkeypatch.setattr(caldav, "delete_events_with_tag",
                        lambda d, tag=caldav.AGENT_DESC_TAG: deletes.append(tag) or 1)

    result = await write_schedule_to_calendar(D)

    assert result["ok"] is True
    assert result["unchanged"] == 1           # A left in place
    assert sorted(writes) == ["B", "C"]       # A NOT rewritten; B rewritten, C added
    assert result["written"] == 2
    assert result["deleted"] == 2             # B (changed) + D (removed)


async def test_failed_delete_does_not_write_duplicate(monkeypatch, _clean_cache):
    _install_schedule()
    monkeypatch.setattr(caldav, "fetch_agent_events", lambda d, prefix: [
        _existing("a", "A", 9),
        _existing("b", "B", 10, event_title="B-old"),   # will fail to delete
    ])
    writes = []
    monkeypatch.setattr(caldav, "write_event",
                        lambda title, s, e, desc="", tag=caldav.AGENT_DESC_TAG: writes.append(title) or "uid")

    b_tag_key = _tk("b", "B")

    def _delete(d, tag=caldav.AGENT_DESC_TAG):
        if b_tag_key in tag:
            raise CalDAVError("simulated delete failure")
        return 1
    monkeypatch.setattr(caldav, "delete_events_with_tag", _delete)

    result = await write_schedule_to_calendar(D)

    assert result["ok"] is False
    # B's delete failed → B must NOT be written (no duplicate); C still added.
    assert "B" not in writes
    assert "C" in writes
    assert any(f["op"] == "change-delete" and f["block_key"] == "b::B" for f in result["failed"])


async def test_not_configured_reports_error(monkeypatch, _clean_cache):
    _install_schedule()
    def _boom(d, prefix):
        raise caldav.CalDAVNotConfigured("no creds")
    monkeypatch.setattr(caldav, "fetch_agent_events", _boom)

    result = await write_schedule_to_calendar(D)
    assert result["ok"] is False
    assert result["error"] == "caldav_not_configured"
    assert result["written"] == 0 and result["deleted"] == 0
