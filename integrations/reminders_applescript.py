"""
Read the macOS Reminders.app inbox via AppleScript.

This is the FALLBACK path. Normally the Swift client reads reminders through
EventKit and uploads them with each generate/stream request (api/tasks.py
`ingest_reminders`); this exists for a backend running without that client.

It is slow — 38-51s measured against a real library, because Reminders.app
answers each property over Apple Events. That cost is why the read moved to the
client.

It used to live in `integrations/caldav_client.py`, which made it look like a
CalDAV feature. It is not: it talks to the local Reminders.app and has nothing
to do with iCloud or CalDAV. That misfiling is why "CalDAV" kept appearing in
traces of slow requests long after the CalDAV paths themselves were gone.
"""
import logging
import subprocess
from datetime import datetime

_log = logging.getLogger("dayflow")


def _fetch_reminders_applescript() -> list[dict]:
    """
    Read incomplete reminders from macOS Reminders.app via AppleScript.
    Returns a list of reminder dicts compatible with _parse_todo() output.
    Falls back to [] on any error (permission denied, app not available, etc.).
    """
    # One reminder per RECORD, pipe-delimited:
    # list_name|title|due_date_iso_or_empty|priority_0_to_9|body_or_empty
    # Records are separated by ASCII 30 (record separator), not newline: a
    # reminder's body may contain newlines, which split one record across lines
    # and left the continuation lines to be discarded as malformed — silently
    # truncating the body. The body is not cosmetic: it supplies the title for
    # reminders left at the default name, and feeds instant classification.
    # (The old code carried a comment claiming newlines were replaced; nothing
    # replaced them.) Pipes inside a body are harmless — the parser splits at
    # most 4 times, so everything after the 4th pipe stays in the body.
    script = r"""
tell application "Reminders"
    set sep to "|"
    set nl to ASCII character 30
    set output to ""
    repeat with aList in every list
        set listName to name of aList
        repeat with r in every reminder of aList
            if completed of r is false then
                set rTitle to name of r
                set rDue to ""
                set rPri to 0
                set rBody to ""
                with timeout of 4 seconds
                    try
                        set rDueDate to due date of r
                        -- format as ISO: YYYY-MM-DDTHH:MM:SS
                        set y to year of rDueDate as string
                        set mo to month of rDueDate as integer
                        set d to day of rDueDate as integer
                        set h to hours of rDueDate as integer
                        set mi to minutes of rDueDate as integer
                        if mo < 10 then set mo to "0" & mo
                        if d < 10 then set d to "0" & d
                        if h < 10 then set h to "0" & h
                        if mi < 10 then set mi to "0" & mi
                        set rDue to y & "-" & mo & "-" & d & "T" & h & ":" & mi & ":00"
                    on error
                        set rDue to ""
                    end try
                end timeout
                with timeout of 2 seconds
                    try
                        set rPri to priority of r as integer
                    on error
                        set rPri to 0
                    end try
                end timeout
                with timeout of 2 seconds
                    try
                        set rBody to body of r
                        if rBody is missing value then set rBody to ""
                    on error
                        set rBody to ""
                    end try
                end timeout
                set output to output & listName & sep & rTitle & sep & rDue & sep & rPri & sep & rBody & nl
            end if
        end repeat
    end repeat
    return output
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return []
        raw_output = result.stdout.strip()
        if not raw_output:
            return []
    except Exception:
        return []

    reminders: list[dict] = []
    for line in raw_output.split("\x1e"):
        parts = line.split("|", 4)
        if len(parts) < 4:
            continue
        list_name, title, due_str, pri_str = parts[0], parts[1], parts[2], parts[3]
        body = parts[4] if len(parts) > 4 else ""

        if not title.strip():
            continue

        if is_system_list(list_name):
            continue
        if _is_spam_reminder(title, body or None):
            continue

        # Parse due date — preserve full datetime so callers can use the time
        deadline = None
        deadline_dt = None
        if due_str:
            try:
                dt = datetime.fromisoformat(due_str)
                deadline = dt.date()
                deadline_dt = dt
            except ValueError:
                pass

        # Map priority (0=none→medium, 1=high, 5=medium, 9=low; AppleScript 1-3=high, 5=medium, 7-9=low)
        try:
            pri_val = int(pri_str)
        except ValueError:
            pri_val = 0
        priority = ("high" if 1 <= pri_val <= 4
                    else "low" if 6 <= pri_val <= 9
                    else "medium")

        reminders.append({
            "id": f"as_{list_name}_{title}_{due_str or 'nodue'}",
            "title": title.strip(),
            "description": body.strip() or None,
            "priority": priority,
            "deadline": deadline,
            "deadline_dt": deadline_dt,
            "source_list": list_name,
        })

    return reminders


_SYSTEM_LIST_NAMES = {
    # Apple system lists
    "提醒 ⚠️", "reminders ⚠️",
    "siri建议", "siri suggestions",
    "生日", "birthdays",
    "中国大陆节假日", "canadian holidays", "holidays in canada",
    "us holidays",
    # Non-scheduling lists to skip
    "shopping list", "shopping", "groceries", "grocery list",
    "购物清单", "购物", "买菜",
}

# Strings that appear in Apple-generated system reminder descriptions
_SPAM_DESCRIPTION_MARKERS = (
    "support.apple.com",
    "support.google.com",
)

# Exact title matches for Apple's built-in placeholder reminders
_SPAM_TITLE_EXACT = {
    "在哪里可以找到我的提醒事项？",
    "此列表的创建者已升级这些提醒事项。",
    "where can i find my reminders?",
    "the list creator has upgraded these reminders.",
}


def _is_spam_reminder(title: str, description: str | None) -> bool:
    """Return True if this reminder looks like an Apple system placeholder."""
    if title.strip().lower() in {t.lower() for t in _SPAM_TITLE_EXACT}:
        return True
    if description:
        desc_lower = description.lower()
        if any(marker in desc_lower for marker in _SPAM_DESCRIPTION_MARKERS):
            return True
    return False


def is_system_list(list_name: str) -> bool:
    """Return True if this reminder list name is an Apple system list."""
    return list_name.strip().lower() in _SYSTEM_LIST_NAMES


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────


def fetch_reminders() -> list[dict]:
    """Every incomplete reminder from Reminders.app.

    Returns the same shape the EventKit upload is mapped into, so
    `do_sync_reminders` can't tell the two sources apart.
    """
    return _fetch_reminders_applescript()
