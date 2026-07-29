# dayflow — Health-Aware AI Scheduling Agent

FastAPI + LangGraph backend with two frontends: a native SwiftUI macOS client
(`cal_swift_frontend/`, the primary one) and the older React/Vite web UI, which
now lags behind. Per-day energy curve
drives task placement; LLM (Claude via LangChain) handles task decomposition and
chat-based adjustments. **Phase 4** pivoted calendar/reminder I/O to local
**EventKit** in the Swift client — the backend is pure logic and no longer talks
to iCloud/CalDAV (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §0).

## Daily startup

The native SwiftUI client (`cal_swift_frontend/`) is the primary frontend. The
React/Vite web UI still runs but lags behind: Phase 4 work (local EventKit
calendar/reminder I/O, project layer, multi-day planning, daily carryover) is
built against the Swift client first, and only part of it is wired into the web
UI. Use the web UI for a quick look; use the Swift client for the current
feature set.

Two terminals, both run from the project root:

```bash
# Terminal 1 — backend (FastAPI on :8000)
.venv/bin/uvicorn main:app --reload

# Terminal 2 — Swift client (build caveats in "Swift frontend" below)
cd cal_swift_frontend
./make_app.sh            # SwiftPM build → ScheduleAgent.app + ad-hoc codesign
open ScheduleAgent.app   # launch via LaunchServices so TCC can prompt
```

**Stopping the backend.** If it's in the foreground, `Ctrl+C`. If you lost the terminal, kill whatever holds port 8000:

```bash
lsof -ti:8000 | xargs kill        # graceful
lsof -ti:8000 | xargs kill -9     # force, if it won't die
```

(`--reload` runs a parent + worker, so you'll usually see two PIDs — the command above kills both.) Stop the Swift client with `pkill -f ScheduleAgentApp`; the web UI stops the same way on port 5173.

### Swift frontend (native macOS client — primary)

A native SwiftUI sidebar lives in `cal_swift_frontend/` and carries the current feature set. It talks to the **same** backend on :8000, so start the backend first.

**Build and run — do NOT use `swift run`.** The app reads/writes the system
Calendar and Reminders via **EventKit**, and macOS only grants those permissions
(TCC) to a real, code-signed `.app` bundle — a bare `swift run` binary is always
denied with no prompt. Package it into a bundle instead:

```bash
cd cal_swift_frontend
./make_app.sh            # SwiftPM build → ScheduleAgent.app + ad-hoc codesign
open ScheduleAgent.app   # launch via LaunchServices so TCC can prompt

# after editing Swift: re-run make_app.sh, then open again
pkill -f ScheduleAgentApp   # stop
```

`make_app.sh` writes `ScheduleAgent.app` next to the sources; its `.build/`
(~270 MB of compile artifacts, not source) stays out of git.

#### Calendar & Reminders permissions

The app requests **full access** to Calendar and Reminders (usage strings are in
`cal_swift_frontend/Info.plist`; the bundle id is `com.dayflow.scheduleagent`).

- The prompts fire the first time the app actually writes — the first **import /
  "写入" (reminders)** and the first **schedule generation (calendar read)**.
- Schedule generation only *reads* the local calendar when access is **already
  granted** (it never blocks on a permission dialog); until then it degrades
  gracefully. Grant access via an import/write once and it sticks.
- **Ad-hoc signing caveat:** re-packaging with `make_app.sh` can reset the TCC
  grant (the signature isn't stable), so macOS may re-prompt after a rebuild.
  A notarized, stably-signed build would fix this — not done yet.
- Reset the grants manually with: `tccutil reset Calendar com.dayflow.scheduleagent`
  and `tccutil reset Reminders com.dayflow.scheduleagent`.

### Web frontend (React/Vite — lagging behind)

The original web UI still runs but trails the Swift client: it has no local
EventKit path, and newer Phase 4 features (project layer, multi-day planning,
daily carryover) are only partly wired in. Keep it for a quick browser view.

```bash
cd frontend && pnpm dev
```

Open <http://localhost:5173> (backend must be running on :8000).

## First-time setup

```bash
# Python backend
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env: ANTHROPIC_API_KEY is the only required value.
# CALDAV_* is legacy — Phase 4 moved calendar/reminder I/O to local EventKit in
# the Swift client (see docs/ARCHITECTURE.md §0); leave it unset unless you run
# the older CalDAV path.

# Frontend (React/Vite — the original web client)
cd frontend
pnpm install
```

The native macOS Swift client needs no extra install step beyond a Swift
toolchain (Xcode / command-line tools) — see the Swift frontend section above.

## Run the tests

```bash
.venv/bin/python -m pytest -q          # full suite (~1s, no network/LLM)
.venv/bin/python -m pytest tests/test_schedule_graph.py -v   # one file
```

All external calls (Claude, CalDAV, AppleScript Reminders) are mocked — tests stay offline.

## Visualize the LangGraph pipelines

```bash
.venv/bin/python scripts/visualize_graphs.py           # ASCII to stdout
.venv/bin/python scripts/visualize_graphs.py mermaid   # mermaid markdown
.venv/bin/python scripts/visualize_graphs.py png       # writes docs/*.png
```

## Project layout

- `main.py` — FastAPI entry point
- `agents/` — Individual agents (`task_agent`, `chat_agent`, `health_agent`, `scheduler_agent`, `calendar_agent`) + LangGraph node wrappers in `nodes.py`
- `graphs/` — LangGraph state graphs (`schedule_graph`, `adjust_graph`, `schedule_stream`)
- `api/` — FastAPI route handlers
- `models/` — Pydantic schemas (Task, Subtask, TimeBlock, DaySchedule, …)
- `storage.py` — JSON-backed in-memory stores (health, tasks, schedules)
- `integrations/caldav_client.py` — iCloud CalDAV adapter
- `frontend/` — React/Vite UI (Today / Tasks / Chat / Settings)
- `cal_swift_frontend/` — native SwiftUI macOS client (EventKit, Phase 4 direction)
- `tests/` — pytest suite (286 tests, fully offline)

## Phase status

Current: **Phase 4 (in progress)** — pure-local pivot: the Swift client owns all
Calendar/Reminders I/O via EventKit, the backend is pure logic returning
create/update/delete change-sets (no iCloud/CalDAV). Project layer + multi-day
planning + daily carryover. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §0
(most current) and [docs/phase3-plan.md](docs/phase3-plan.md) for the roadmap.

## Optional LangSmith tracing

Add to `.env` for free trace visualization of every graph run and Claude call:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=dayflow
```

Restart uvicorn — traces appear at <https://smith.langchain.com>.
