"""
Calendar Agent — classifies events and extracts free windows from a date's calendar.
Phase 2: reads real events from CalDAV (iCloud Calendar).
"""
import asyncio
from datetime import date, datetime, timedelta

from integrations.caldav_client import fetch_events
from models.schedule import BlockType, FreeWindow, TimeBlock
from models.task import CognitiveLoad

_AGENT_TAG = "[agent-scheduled]"


def classify_event(event: dict) -> BlockType:
    """
    Any event explicitly added to the calendar is treated as a fixed block —
    the user put it there intentionally so we should respect it regardless of
    the event title language or content.
    Only events written by this agent (tagged [agent-scheduled]) are treated
    as scheduled (moveable) blocks.
    """
    description = event.get("description", "")
    if description and _AGENT_TAG in description:
        return BlockType.scheduled
    return BlockType.fixed


async def fetch_fixed_blocks(
    target_date: date,
    work_start: int = 8,
    work_end: int = 22,
) -> tuple[list[TimeBlock], list[FreeWindow]]:
    """
    Fetch real CalDAV events, classify them, and return (fixed_blocks, free_windows).
    Falls back to empty if CalDAV credentials are not configured.
    """
    raw_events = await asyncio.to_thread(fetch_events, target_date)
    fixed_blocks = events_to_fixed_blocks(raw_events, target_date)
    free_windows = extract_free_windows(fixed_blocks, target_date, work_start, work_end)
    return fixed_blocks, free_windows


def events_to_fixed_blocks(events: list[dict], target_date: date) -> list[TimeBlock]:
    """Convert raw calendar event dicts to TimeBlock objects (fixed only)."""
    blocks: list[TimeBlock] = []
    for ev in events:
        if classify_event(ev) != BlockType.fixed:
            continue
        start = _parse_dt(ev["start"], target_date)
        end = _parse_dt(ev["end"], target_date)
        blocks.append(
            TimeBlock(
                start=start,
                end=end,
                block_type=BlockType.fixed,
                title=ev.get("title", "Busy"),
                cognitive_load=None,
                notes=ev.get("description"),
            )
        )
    return sorted(blocks, key=lambda b: b.start)


def extract_free_windows(
    fixed_blocks: list[TimeBlock],
    target_date: date,
    work_start: int = 8,
    work_end: int = 22,
) -> list[FreeWindow]:
    """
    Return contiguous free windows between fixed blocks within [work_start, work_end].
    Minimum window size: 25 minutes.
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day, work_start, 0)
    # work_end=24 means "end of day" — use next-day midnight (timedelta avoids month/year edge cases)
    if work_end >= 24:
        day_end = datetime(target_date.year, target_date.month, target_date.day) + timedelta(days=1)
    else:
        day_end = datetime(target_date.year, target_date.month, target_date.day, work_end, 0)

    # Build sorted list of busy intervals clamped to work hours
    busy: list[tuple[datetime, datetime]] = []
    for b in fixed_blocks:
        s = max(b.start, day_start)
        e = min(b.end, day_end)
        if e > s:
            busy.append((s, e))
    busy.sort(key=lambda x: x[0])

    # Merge overlapping busy intervals
    merged: list[tuple[datetime, datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find gaps
    windows: list[FreeWindow] = []
    cursor = day_start
    for s, e in merged:
        if s > cursor:
            _append_window(windows, cursor, s)
        cursor = max(cursor, e)
    if cursor < day_end:
        _append_window(windows, cursor, day_end)

    return windows


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _append_window(windows: list[FreeWindow], start: datetime, end: datetime) -> None:
    duration = int((end - start).total_seconds() / 60)
    if duration >= 25:
        windows.append(
            FreeWindow(
                start_hour=start.hour,
                start_minute=start.minute,
                end_hour=end.hour,
                end_minute=end.minute,
                duration_minutes=duration,
                energy_score=0.0,  # scored later by health_agent.score_windows
            )
        )


def _parse_dt(value: str | datetime, fallback_date: date) -> datetime:
    if isinstance(value, datetime):
        return value
    # Try ISO format
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Treat as HH:MM
        h, m = map(int, value.split(":"))
        return datetime(fallback_date.year, fallback_date.month, fallback_date.day, h, m)
