"""
Client-uploaded reminders (EventKit) → task_store.

Replaces the backend reading Reminders.app over AppleScript, which measured
38-51s per call. The mapping has to match what that parser produced, since
do_sync_reminders can't tell the two sources apart.
"""
from datetime import date

import pytest

import storage
from api.tasks import ReminderInput, _reminder_input_to_raw, ingest_reminders


def _r(**kw):
    kw.setdefault("title", "交房租")
    return ReminderInput(**kw)


def test_priority_maps_like_the_applescript_parser():
    assert _reminder_input_to_raw(_r(priority=0))["priority"] == "medium"
    assert _reminder_input_to_raw(_r(priority=1))["priority"] == "high"
    assert _reminder_input_to_raw(_r(priority=4))["priority"] == "high"
    assert _reminder_input_to_raw(_r(priority=5))["priority"] == "medium"
    assert _reminder_input_to_raw(_r(priority=9))["priority"] == "low"


def test_due_becomes_both_date_and_datetime():
    raw = _reminder_input_to_raw(_r(due="2026-10-20T14:30:00"))
    assert raw["deadline"] == date(2026, 10, 20)
    assert raw["deadline_dt"].hour == 14 and raw["deadline_dt"].minute == 30


def test_unparsable_due_is_dropped_not_fatal():
    raw = _reminder_input_to_raw(_r(due="whenever"))
    assert raw["deadline"] is None and raw["deadline_dt"] is None


def test_eventkit_identifier_becomes_the_task_id():
    """
    EventKit's identifier is stable across renames. The AppleScript path had to
    synthesise an id from list+title+due, so editing a reminder's title there
    read as a delete plus an add — losing anything keyed to the old id.
    """
    raw = _reminder_input_to_raw(_r(identifier="EK-ABC", title="交房租"))
    assert raw["id"] == "EK-ABC"
    renamed = _reminder_input_to_raw(_r(identifier="EK-ABC", title="交这个月房租"))
    assert renamed["id"] == raw["id"]


def test_missing_identifier_falls_back_to_a_synthesised_id():
    raw = _reminder_input_to_raw(_r(identifier=None, list_name="Inbox", due=None))
    assert raw["id"].startswith("ek_Inbox_")


def test_empty_notes_become_none():
    assert _reminder_input_to_raw(_r(notes="   "))["description"] is None
    assert _reminder_input_to_raw(_r(notes="记得带合同"))["description"] == "记得带合同"


@pytest.mark.asyncio
async def test_ingest_upserts_and_silences_the_applescript_fallback(clean_stores):
    from agents import nodes

    nodes._last_sync_ts = 0.0
    result = await ingest_reminders([
        _r(identifier="EK-1", title="交房租", priority=1),
        _r(identifier="EK-2", title="写周报", priority=5),
    ])

    assert result["added"] == 2
    titles = {t.title for t in storage.task_store.values()}
    assert titles == {"交房租", "写周报"}
    assert all(t.source == "reminders" for t in storage.task_store.values())
    # The throttle is now satisfied, so generation won't go run AppleScript.
    assert nodes._last_sync_ts > 0.0


@pytest.mark.asyncio
async def test_ingest_replaces_rather_than_accumulates(clean_stores):
    """A reminder completed or deleted on the client must disappear here too."""
    await ingest_reminders([_r(identifier="EK-1", title="交房租"),
                            _r(identifier="EK-2", title="写周报")])
    await ingest_reminders([_r(identifier="EK-1", title="交房租")])

    titles = {t.title for t in storage.task_store.values()}
    assert titles == {"交房租"}
