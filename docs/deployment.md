# Deployment modes

This project runs in one of two modes, controlled by a single env var:

```
DEPLOYMENT_MODE=local   # default
DEPLOYMENT_MODE=cloud   # public deployment, e.g. Railway
```

There are **no parallel code paths** for the two modes. Almost every difference
is configuration, which is consolidated in [`config.py`](../config.py).

## What actually differs

| Aspect | local | cloud |
|---|---|---|
| **CORS origins** ([`config.py`](../config.py)) | `localhost:5173` only | `*` |
| **iCloud Reminders.app** ([`integrations/caldav_client.py`](../integrations/caldav_client.py) line 74) | ✅ AppleScript reads Reminders.app | ❌ Linux container has no AppleScript — only CalDAV fallback, which iCloud blocks |
| **iCloud Calendar** | ✅ CalDAV HTTP | ✅ CalDAV HTTP |
| **iPhone Shortcuts target URL** | `http://<LAN-IP>:8000` (needs same WiFi) | Public Railway URL (works anywhere) |
| **Secrets storage** | [`.env`](../.env) file | Railway dashboard → Variables |
| **Start command** | `uvicorn main:app --reload` | [`docs/deployment/railway.toml`](deployment/railway.toml) → `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Persistence** | [`data/*.json`](../data) in repo | Container filesystem (ephemeral unless volume) |

The only true platform branch in the code is the `sys.platform == "darwin"` check in `caldav_client._fetch_reminders_applescript`. Everything else just reads env vars.

## Local mode (default — recommended)

```bash
# backend
uvicorn main:app --reload

# frontend
cd frontend && npm run dev
```

Open <http://localhost:5173>.

## Cloud mode (Railway, etc.)

Local mode is actually **more complete** than cloud mode because Reminders.app
sync only works on macOS. Cloud mode is documented as a fallback.

See [`docs/deployment/README.md`](deployment/README.md) for re-enabling Railway.

## Why local is more powerful here

iCloud has two CalDAV homes:

- `/calendars/` — VEVENT (Calendar.app) — works over CalDAV ✅
- `/reminders/` — VTODO (Reminders.app) — iCloud blocks all PROPFIND with HTTP 400 ❌

The only way to read iCloud Reminders programmatically is via the macOS
Reminders.app, which is reachable via AppleScript only on macOS. So local
running on a Mac is the **only** environment where the agent can see your
reminders directly. On Railway it can only consume reminders that you've
pushed in via `/tasks/push-reminder` (iPhone Shortcut workflow).
