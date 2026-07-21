"""
CalDAV integration — fetches events and reminders from iCloud.

iCloud uses two separate CalDAV homes:
  /calendars/  → VEVENT (Calendar.app events)
  /reminders/  → VTODO  (Reminders.app items)

NOTE: iCloud's /reminders/ CalDAV home rejects all PROPFIND requests (400).
The public CalDAV interface does not expose Reminders.app lists. As a result,
we fall back to reading Reminders directly from the local macOS Reminders.app
via AppleScript when CalDAV discovery yields no VTODO collections.

Credentials (env vars):
  CALDAV_URL      = https://caldav.icloud.com
  CALDAV_USERNAME = Apple ID email
  CALDAV_PASSWORD = App-Specific Password (appleid.apple.com)
"""
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import caldav
import recurring_ical_events
from icalendar import Calendar as ICalendar, Event as IEvent


# Tag embedded in DESCRIPTION of agent-written events.
# Existing classify_event() in agents/calendar_agent.py looks for the substring
# "[agent-scheduled]" — this tag preserves that prefix so re-reads still classify
# our blocks as `scheduled` (moveable) rather than `fixed` (user events).
AGENT_DESC_TAG = "[agent-scheduled:dayflow]"


# ──────────────────────────────────────────────────────────────────────────────
# Errors + bounded retry (Step 0 hardening)
#
# The write path used to swallow every exception (`except: pass` → return None/0),
# which made a failed delete indistinguishable from "nothing to delete". Combined
# with delete-then-write that was optimistic about the delete, a transient delete
# failure + a successful write produced DUPLICATE events, invisibly. We now:
#   - validate args before hitting the network,
#   - retry transient failures (network/timeout/5xx/rate-limit) a few times,
#   - raise a structured error on hard failure so callers can report it.
# 4xx (auth/not-found/bad-request) is NOT retried — retrying won't help.
# ──────────────────────────────────────────────────────────────────────────────

class CalDAVError(Exception):
    """A CalDAV network/server operation failed after exhausting retries."""


class CalDAVNotConfigured(CalDAVError):
    """CalDAV credentials are missing or no writable calendar is reachable."""


_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (0.5, 1.0, 2.0)  # seconds to sleep before each retry


def _status_of(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP status code from a caldav/httpx error."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None) or getattr(exc, "reason", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc: Exception) -> bool:
    """Retry transient failures only. Bad-request / auth / not-found are terminal."""
    # Programmer errors and explicit not-configured are never retried.
    if isinstance(exc, (ValueError, CalDAVNotConfigured)):
        return False
    try:
        from caldav.lib import error as _cderr
        if isinstance(exc, (_cderr.AuthorizationError, _cderr.NotFoundError,
                            _cderr.ConsistencyError, _cderr.ETagMismatchError)):
            return False
        if isinstance(exc, _cderr.RateLimitError):
            return True
    except Exception:
        pass
    status = _status_of(exc)
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        return False
    return True  # network/timeout/5xx/unknown → worth a retry


def _with_retry(fn, what: str):
    """Run `fn`, retrying transient CalDAV errors with exponential backoff."""
    last: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified by _is_retryable
            last = exc
            if attempt == _RETRY_ATTEMPTS - 1 or not _is_retryable(exc):
                break
            time.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
    if isinstance(last, (ValueError, CalDAVError)):
        raise last
    raise CalDAVError(f"{what} failed: {last}") from last


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def fetch_events(target_date: date) -> list[dict]:
    """
    Return VEVENT items for target_date, including recurring event instances.
    iCloud doesn't support REPORT CALENDAR-QUERY (date_search), so we fetch
    all objects from each calendar and use recurring_ical_events to expand
    recurrences client-side.
    """
    client = _make_client()
    if client is None:
        return []

    events: list[dict] = []
    try:
        for cal in _event_calendars(client):
            for item in _load_objects(cal):
                parsed_list = _parse_event_recurring(item.data, target_date)
                events.extend(parsed_list)
    except Exception:
        pass
    return events


def fetch_reminders() -> list[dict]:
    """
    Return incomplete VTODO items from iCloud Reminders.

    iCloud's public CalDAV interface blocks all /reminders/ access (HTTP 400).
    Strategy A: CalDAV — only finds the 提醒⚠️ spam collection under /calendars/.
    Strategy B: macOS AppleScript — reads directly from Reminders.app (authoritative).
    """
    # Strategy A: CalDAV (collects any VTODO-capable collections under /calendars/)
    reminders: list[dict] = _fetch_reminders_caldav()

    # Strategy B: macOS Reminders.app via AppleScript
    if sys.platform == "darwin":
        applescript_items = _fetch_reminders_applescript()
        if applescript_items:
            # Merge: AppleScript is authoritative — replace CalDAV results entirely
            return applescript_items

    return reminders


def _fetch_reminders_caldav() -> list[dict]:
    """Pull VTODOs via CalDAV (only works for the 提醒⚠️ spam collection on iCloud)."""
    client = _make_client()
    if client is None:
        return []

    reminders: list[dict] = []
    try:
        for col in _reminder_collections(client):
            col_name = _cal_name(col)
            if is_system_list(col_name):
                continue
            for obj in _load_objects(col):
                parsed = _parse_todo(obj)
                if parsed:
                    parsed["source_list"] = col_name
                    reminders.append(parsed)
    except Exception:
        pass
    return reminders


def _fetch_reminders_applescript() -> list[dict]:
    """
    Read incomplete reminders from macOS Reminders.app via AppleScript.
    Returns a list of reminder dicts compatible with _parse_todo() output.
    Falls back to [] on any error (permission denied, app not available, etc.).
    """
    # AppleScript outputs one reminder per line, pipe-delimited:
    # list_name|title|due_date_iso_or_empty|priority_0_to_9|body_or_empty
    script = r"""
tell application "Reminders"
    set sep to "|"
    set nl to ASCII character 10
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
                -- Replace pipe and newline in fields to avoid parsing errors
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
    for line in raw_output.splitlines():
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


def fetch_debug_info(target_date: date) -> dict:
    """Extended diagnostics for the /calendar/debug endpoint."""
    client = _make_client()
    if client is None:
        return {"status": "not_configured", "calendars": [], "events": [], "reminders": []}

    start_utc, end_utc = _utc_window(target_date)

    result: dict = {
        "status": "ok",
        "calendar_home": None,
        "reminder_home": None,
        "calendars": [],
        "reminder_collections": [],
        "event_count": 0,
        "events": [],
        "reminder_count": 0,
        "reminders": [],
        "errors": [],
    }

    # ── Discover homes ────────────────────────────────────────────────────────
    try:
        principal = client.principal()
        cal_home_url, rem_home_url = _discover_homes(principal)
        result["calendar_home"] = cal_home_url
        result["reminder_home"] = rem_home_url
    except Exception as exc:
        result["errors"].append(f"principal(): {exc}")
        result["status"] = "error"
        return result

    # ── Event calendars ───────────────────────────────────────────────────────
    try:
        event_cals = _event_calendars(client)
        for cal in event_cals:
            name = _cal_name(cal)
            errors: list[str] = []
            raw = _load_objects(cal)
            found = []
            for item in raw:
                for parsed in _parse_event_recurring(item.data, target_date):
                    found.append({
                        "title": parsed["title"],
                        "start": str(parsed["start"]),
                        "end": str(parsed["end"]),
                    })
            result["calendars"].append({
                "name": name,
                "raw_count": len(raw),
                "event_count": len(found),
                "errors": errors,
            })
            result["events"].extend(found)
        result["event_count"] = len(result["events"])
    except Exception as exc:
        result["errors"].append(f"event_calendars: {exc}")

    # ── Reminder collections (Strategy A: sub-collection enumeration) ────────
    try:
        rem_cols, rem_discovery_errors = _reminder_collections_verbose(client)
        result["errors"].extend(rem_discovery_errors)
        for col in rem_cols:
            name = _cal_name(col)
            col_todos: list[dict] = []
            col_errors: list[str] = []
            try:
                for obj in _load_objects(col):
                    parsed = _parse_todo(obj)
                    if parsed:
                        col_todos.append({"title": parsed["title"], "priority": parsed["priority"]})
            except Exception as exc:
                col_errors.append(str(exc))
            result["reminder_collections"].append({
                "name": name,
                "url": str(col.url),
                "todo_count": len(col_todos),
                "errors": col_errors,
            })
            result["reminders"].extend(col_todos)
        result["reminder_count"] = len(result["reminders"])
    except Exception as exc:
        result["errors"].append(f"reminder_collections: {exc}")

    # ── Reminder fallback (Strategy B: objects() on reminders home) ──────────
    if not result["reminders"] and result.get("reminder_home"):
        rem_home_url = result["reminder_home"]
        try:
            rem_home = client.calendar(url=rem_home_url)
            objs = _load_objects(rem_home)
            result["errors"].append(f"Strategy B (objects on rem_home): {len(objs)} objects")
            found_b: list[dict] = []
            for obj in objs:
                parsed = _parse_todo(obj)
                if parsed:
                    found_b.append({"title": parsed["title"], "priority": parsed["priority"]})
            if found_b:
                result["reminder_collections"].append({
                    "name": "reminders_home_direct",
                    "url": rem_home_url,
                    "todo_count": len(found_b),
                    "errors": [],
                })
                result["reminders"].extend(found_b)
                result["reminder_count"] = len(result["reminders"])
        except Exception as exc:
            result["errors"].append(f"Strategy B (objects on rem_home): {exc}")

    # ── objects() spot-check on first event calendar ──────────────────────────
    try:
        first_cals = _event_calendars(client)
        if first_cals:
            loaded = _load_objects(first_cals[0])
            result["first_calendar_object_count"] = len(loaded)
            if loaded:
                result["first_calendar_sample"] = str(loaded[0].data)[:400]
        else:
            result["first_calendar_object_count"] = 0
    except Exception as exc:
        result["errors"].append(f"objects() spot-check: {exc}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Write-back (Phase 3)
# ──────────────────────────────────────────────────────────────────────────────

def write_event(
    title: str,
    start: datetime,
    end: datetime,
    description: str = "",
    tag: str = AGENT_DESC_TAG,
) -> str:
    """
    Create a VEVENT on the user's iCloud calendar tagged as agent-written.
    Returns the event UID on success.

    Raises:
        ValueError            — invalid arguments (empty title, end <= start).
        CalDAVNotConfigured   — no credentials / no writable calendar.
        CalDAVError           — the save failed after exhausting retries.

    `tag` is appended to the DESCRIPTION so callers can later identify and
    remove the event. Per-block syncs use a unique tag per block so they don't
    interfere with each other; full-day syncs all share the bare AGENT_DESC_TAG
    prefix and can be cleaned in one shot.
    """
    # ── Parameter validation (before touching the network) ──────────────────
    if not title or not title.strip():
        raise ValueError("write_event: title must be non-empty")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("write_event: start and end must be datetimes")
    if end <= start:
        raise ValueError(f"write_event: end ({end}) must be after start ({start})")

    client = _make_client()
    if client is None:
        raise CalDAVNotConfigured("CalDAV credentials not set")
    cal = _writable_calendar(client)
    if cal is None:
        raise CalDAVNotConfigured("No writable iCloud calendar reachable")

    full_desc = f"{description}\n{tag}" if description else tag
    uid = str(uuid4())

    cal_obj = ICalendar()
    cal_obj.add("prodid", "-//Dayflow//AgentScheduler//EN")
    cal_obj.add("version", "2.0")

    event = IEvent()
    event.add("summary", title)
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("description", full_desc)
    event.add("uid", uid)
    event.add("dtstamp", datetime.now())
    cal_obj.add_component(event)

    _with_retry(lambda: cal.save_event(cal_obj.to_ical().decode()), "write_event")
    return uid


def _event_in_range(ical, start: date | None, end: date | None) -> bool:
    """True if the event occurs within [start, end] (inclusive). None bound = unbounded."""
    if start is None and end is None:
        return True
    lo = start or date(1970, 1, 1)
    hi = (end or date(2999, 12, 31)) + timedelta(days=1)
    try:
        return bool(recurring_ical_events.of(ical).between(
            datetime(lo.year, lo.month, lo.day),
            datetime(hi.year, hi.month, hi.day),
        ))
    except Exception:
        return False


def _event_description(ical) -> str:
    for component in ical.walk("VEVENT"):
        return str(component.get("DESCRIPTION", "") or "")
    return ""


def delete_events_by_tags(
    match_all: list[str],
    start: date | None = None,
    end: date | None = None,
) -> int:
    """
    Delete every event whose DESCRIPTION contains ALL substrings in `match_all`
    and that occurs within [start, end] (inclusive; None = unbounded — used for
    cross-day project cleanup).

    Returns the number of events deleted. Raises CalDAVError if a matched event
    could not be deleted after retries (so callers never assume a delete that
    silently failed) or if the calendar could not be listed.
    """
    client = _make_client()
    if client is None:
        raise CalDAVNotConfigured("CalDAV credentials not set")
    if not match_all:
        raise ValueError("delete_events_by_tags: match_all must be non-empty")

    def _run() -> int:
        deleted = 0
        for cal in _event_calendars(client):
            for item in _load_objects(cal):
                try:
                    ical = ICalendar.from_ical(item.data)
                except Exception:
                    continue  # unparseable object — skip, not our event
                description = _event_description(ical)
                if not all(tag in description for tag in match_all):
                    continue
                if not _event_in_range(ical, start, end):
                    continue
                # A matched event MUST delete successfully — surface failures.
                _with_retry(item.delete, "delete_event")
                deleted += 1
        return deleted

    # Wrap the whole listing in retry too (transient PROPFIND/network failures).
    return _with_retry(_run, "delete_events_by_tags")


def delete_events_with_tag(target_date: date, tag: str = AGENT_DESC_TAG) -> int:
    """
    Delete all events on target_date whose DESCRIPTION contains `tag`.
    Thin single-date, single-tag wrapper over delete_events_by_tags.
    Returns the number of events deleted; raises CalDAVError on hard failure.
    """
    return delete_events_by_tags([tag], target_date, target_date)


def fetch_agent_events(target_date: date, prefix: str) -> list[dict]:
    """
    Return agent-written events on target_date whose DESCRIPTION contains a
    per-block tag line starting with `prefix` (e.g. "[agent-scheduled:dayflow").

    Each dict: {key, title, start, end, description, tag_line}. `key` is parsed
    from the per-block tag ("[...:dayflow:<key>]" → "<key>"; bare tag → "").
    Used by the write-back diff to decide added/changed/removed/unchanged.
    Raises CalDAVError if the calendar could not be listed after retries.
    """
    client = _make_client()
    if client is None:
        raise CalDAVNotConfigured("CalDAV credentials not set")

    def _run() -> list[dict]:
        found: list[dict] = []
        for cal in _event_calendars(client):
            for item in _load_objects(cal):
                try:
                    ical = ICalendar.from_ical(item.data)
                except Exception:
                    continue
                if not recurring_ical_events.of(ical).at(target_date):
                    continue
                description = _event_description(ical)
                tag_line = next(
                    (ln for ln in description.splitlines() if ln.startswith(prefix)),
                    None,
                )
                if tag_line is None:
                    continue
                ev = _parse_event(item, target_date)
                if ev is None:
                    continue
                # "[...:dayflow:<key>]" → "<key>"; bare "[...:dayflow]" → ""
                inner = tag_line[len(prefix):].rstrip("]")
                key = inner[1:] if inner.startswith(":") else ""
                found.append({
                    "key": key,
                    "title": ev["title"],
                    "start": ev["start"],
                    "end": ev["end"],
                    "description": description,
                    "tag_line": tag_line,
                })
        return found

    return _with_retry(_run, "fetch_agent_events")


def _writable_calendar(client: caldav.DAVClient) -> caldav.Calendar | None:
    """
    Pick a calendar to write to. Honors CALDAV_TARGET_CALENDAR env var
    (exact display-name match); otherwise falls back to the first non-holiday
    non-birthday calendar. Returns None when no event calendars are reachable.
    """
    cals = _event_calendars(client)
    if not cals:
        return None

    target = os.getenv("CALDAV_TARGET_CALENDAR", "").strip()
    if target:
        for cal in cals:
            if _cal_name(cal).strip() == target:
                return cal

    # Skip read-only system calendars
    skip = ("holiday", "birthday", "节假日", "生日", "节日")
    for cal in cals:
        name = _cal_name(cal).strip().lower()
        if any(s in name for s in skip):
            continue
        return cal
    return cals[0]


# ──────────────────────────────────────────────────────────────────────────────
# Discovery helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_client() -> caldav.DAVClient | None:
    url = os.getenv("CALDAV_URL", "").strip()
    username = os.getenv("CALDAV_USERNAME", "").strip()
    password = os.getenv("CALDAV_PASSWORD", "").strip()
    if not (url and username and password):
        return None
    return caldav.DAVClient(url=url, username=username, password=password)


def _discover_homes(principal: caldav.Principal) -> tuple[str | None, str | None]:
    """
    Return (calendar_home_url, reminder_home_url).
    iCloud puts events under /calendars/ and todos under /reminders/.
    We derive one from the other when only one is directly advertised.
    """
    try:
        cal_home = str(principal.calendar_home_set.url)
    except Exception:
        cal_home = None

    if cal_home and "/reminders/" in cal_home:
        # Server advertised reminders home as calendar home — derive calendars URL
        rem_home = cal_home
        cal_home = cal_home.replace("/reminders/", "/calendars/")
    elif cal_home and "/calendars/" in cal_home:
        rem_home = cal_home.replace("/calendars/", "/reminders/")
    else:
        rem_home = None

    return cal_home, rem_home


def _event_calendars(client: caldav.DAVClient) -> list[caldav.Calendar]:
    """Return all VEVENT calendar collections."""
    principal = client.principal()
    cal_home_url, _ = _discover_homes(principal)
    if not cal_home_url:
        return principal.calendars()
    try:
        home = client.calendar(url=cal_home_url)
        children = home.children()
        if children:
            return [client.calendar(url=str(c[0])) for c in children]
        return [home]
    except Exception:
        return principal.calendars()


def _reminder_collections(client: caldav.DAVClient) -> list[caldav.Calendar]:
    cols, _ = _reminder_collections_verbose(client)
    return cols


def _reminder_collections_verbose(
    client: caldav.DAVClient,
) -> tuple[list[caldav.Calendar], list[str]]:
    """
    Return (collections, errors).
    iCloud's calendar_home_set originally points to /reminders/, so
    principal.calendars() discovers reminder lists directly.
    We filter by /reminders/ in the URL to be sure.
    """
    errors: list[str] = []
    principal = client.principal()
    _, rem_home_url = _discover_homes(principal)
    errors.append(f"reminder_home_url={rem_home_url}")

    # Strategy 1: principal.calendars() — iCloud home_set may point to /reminders/
    try:
        all_cals = principal.calendars()
        errors.append(f"principal.calendars() returned {len(all_cals)} items")
        reminder_cals = [c for c in all_cals if "/reminders/" in str(c.url)]
        errors.append(f"filtered to {len(reminder_cals)} /reminders/ collections")
        if reminder_cals:
            return reminder_cals, errors
        # No /reminders/ URLs found via principal.calendars() — fall through to Strategy 2
        errors.append("Strategy 1: no /reminders/ collections found, trying Strategy 2")
    except Exception as exc:
        errors.append(f"Strategy 1 (principal.calendars): {exc}")

    # Strategy 2: .children() on the reminders home (same as _event_calendars)
    if rem_home_url:
        try:
            rem_home = client.calendar(url=rem_home_url)
            children = rem_home.children()
            errors.append(f"Strategy 2 (rem_home.children()): found {len(children)} children")
            if children:
                cols = [client.calendar(url=str(c[0])) for c in children]
                return cols, errors
        except Exception as exc:
            errors.append(f"Strategy 2 (rem_home.children()): {exc}")

    # Strategy 3: manual PROPFIND on reminders home (minimal request)
    if rem_home_url:
        try:
            cols = _propfind_collections(client, rem_home_url)
            errors.append(f"Strategy 3 (manual PROPFIND): found {len(cols)} collections")
            if cols:
                return cols, errors
        except Exception as exc:
            errors.append(f"Strategy 3 (manual PROPFIND): {exc}")

    return [], errors


def _propfind_collections(
    client: caldav.DAVClient, home_url: str
) -> list[caldav.Calendar]:
    """
    Discover child collections via a minimal Depth:1 PROPFIND using httpx.
    caldav 1.3.x doesn't expose a public request() method so we go direct.
    """
    import xml.etree.ElementTree as ET
    import httpx

    username = os.getenv("CALDAV_USERNAME", "").strip()
    password = os.getenv("CALDAV_PASSWORD", "").strip()
    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:propfind xmlns:D="DAV:">'
        '<D:prop><D:displayname/><D:resourcetype/></D:prop>'
        '</D:propfind>'
    )
    response = httpx.request(
        method="PROPFIND",
        url=home_url,
        content=xml_body.encode(),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        auth=(username, password),
        follow_redirects=True,
        timeout=15,
    )
    cols: list[caldav.Calendar] = []
    if response.status_code not in (207, 200):
        raise RuntimeError(f"PROPFIND returned HTTP {response.status_code}: {response.text[:200]}")
    root = ET.fromstring(response.content)
    ns = {"D": "DAV:"}
    for resp in root.findall(".//D:response", ns):
        href_el = resp.find("D:href", ns)
        if href_el is None:
            continue
        href = href_el.text or ""
        if href.rstrip("/") == home_url.rstrip("/"):
            continue  # skip the home itself
        rt = resp.find(".//D:resourcetype", ns)
        if rt is not None and rt.find("D:collection", ns) is not None:
            full_url = _resolve_url(home_url, href)
            cols.append(client.calendar(url=full_url))
    return cols


def _resolve_url(base: str, href: str) -> str:
    """Resolve a potentially relative href against the base URL."""
    if href.startswith("http"):
        return href
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path=href))


# ──────────────────────────────────────────────────────────────────────────────
# Search / iteration
# ──────────────────────────────────────────────────────────────────────────────

def _utc_window(target_date: date) -> tuple[datetime, datetime]:
    """Search ±1 day in UTC to avoid timezone edge cases; filter client-side."""
    start = datetime(target_date.year, target_date.month, target_date.day,
                     0, 0, 0, tzinfo=timezone.utc) - timedelta(days=1)
    return start, start + timedelta(days=3)


def _search_calendar(cal: caldav.Calendar, start: datetime, end: datetime) -> list:
    try:
        return cal.date_search(start=start, end=end, expand=True)
    except Exception:
        pass
    try:
        return cal.date_search(start=start, end=end, expand=False)
    except Exception:
        return []


def _search_calendar_verbose(
    cal: caldav.Calendar, start: datetime, end: datetime, errors: list
) -> list:
    try:
        return cal.date_search(start=start, end=end, expand=True)
    except Exception as e1:
        errors.append(f"date_search(expand=True): {e1}")
    try:
        return cal.date_search(start=start, end=end, expand=False)
    except Exception as e2:
        errors.append(f"date_search(expand=False): {e2}")
    return []


def _load_objects(col: caldav.Calendar) -> list:
    """
    Fetch all objects from a CalDAV collection with their data loaded.
    iCloud returns stubs from objects() — we must call load() on each.
    Uses load_objects=True if supported, falls back to manual loading.
    """
    try:
        objs = list(col.objects(load_objects=True))
        if objs and objs[0].data:
            return objs
    except TypeError:
        pass
    except Exception:
        return []

    # Fallback: load each stub individually
    try:
        stubs = list(col.objects())
        loaded = []
        for stub in stubs:
            try:
                stub.load()
                if stub.data:
                    loaded.append(stub)
            except Exception:
                continue
        return loaded
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_event_recurring(ical_data: str, target_date: date) -> list[dict]:
    """
    Parse a raw iCal string and return all event instances that fall on
    target_date, correctly expanding RRULE recurring events.
    """
    results: list[dict] = []
    try:
        cal = ICalendar.from_ical(ical_data)
        instances = recurring_ical_events.of(cal).at(target_date)
        for component in instances:
            ev = _component_to_event(component, target_date)
            if ev:
                results.append(ev)
    except Exception:
        pass
    return results


def _component_to_event(component, target_date: date) -> dict | None:
    """Convert a VEVENT component (already expanded to the right occurrence) to a dict."""
    try:
        dtstart_prop = component.get("DTSTART")
        if not dtstart_prop:
            return None
        start_dt = _to_naive_local(dtstart_prop.dt, target_date)

        dtend_prop = component.get("DTEND")
        if dtend_prop:
            end_dt = _to_naive_local(dtend_prop.dt, target_date)
        else:
            dur = component.get("DURATION")
            end_dt = start_dt + dur.dt if dur else start_dt + timedelta(hours=1)

        title = str(component.get("SUMMARY", "Busy"))
        description = str(component.get("DESCRIPTION", "") or "")

        attendees: list[str] = []
        att = component.get("ATTENDEE")
        if att:
            attendees = [str(a) for a in att] if isinstance(att, list) else [str(att)]

        return {"title": title, "start": start_dt, "end": end_dt,
                "attendees": attendees, "description": description}
    except Exception:
        return None


def _parse_event(item, target_date: date) -> dict | None:
    """Parse a CalDAV object into an event dict (non-recurring, used by debug path)."""
    try:
        cal = ICalendar.from_ical(item.data)
        for component in cal.walk("VEVENT"):
            return _component_to_event(component, target_date)
    except Exception:
        return None
    return None


def _parse_todo(item) -> dict | None:
    """Parse a CalDAV object into a reminder dict using icalendar."""
    try:
        cal = ICalendar.from_ical(item.data)
        for component in cal.walk("VTODO"):
            status = str(component.get("STATUS", "NEEDS-ACTION")).upper()
            if status == "COMPLETED":
                continue

            title = str(component.get("SUMMARY", "Reminder"))
            description = str(component.get("DESCRIPTION", "")) or None
            uid = str(component.get("UID", ""))

            if _is_spam_reminder(title, description):
                return None

            deadline = None
            deadline_dt = None
            due = component.get("DUE")
            if due:
                due_val = due.dt
                if isinstance(due_val, datetime):
                    local_dt = due_val.astimezone() if due_val.tzinfo else due_val
                    deadline = local_dt.date()
                    deadline_dt = local_dt.replace(tzinfo=None)  # naive local
                elif isinstance(due_val, date):
                    deadline = due_val

            priority_val = int(component.get("PRIORITY", 0))
            priority = ("high" if 1 <= priority_val <= 4
                        else "low" if 6 <= priority_val <= 9
                        else "medium")

            return {"id": uid, "title": title, "description": description,
                    "priority": priority, "deadline": deadline, "deadline_dt": deadline_dt}
    except Exception:
        return None
    return None


# Known Apple system reminder list names — skip all items from these collections
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

def _cal_name(cal: caldav.Calendar) -> str:
    try:
        return str(cal.name)
    except Exception:
        return str(cal.url)


def _overlaps_date(event: dict, target_date: date) -> bool:
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    day_end = day_start + timedelta(days=1)
    return event["start"] < day_end and event["end"] > day_start


def _to_naive_local(value, fallback_date: date) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 0, 0, 0)
    return datetime(fallback_date.year, fallback_date.month, fallback_date.day, 0, 0, 0)
