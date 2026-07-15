# dayflow — Health-Aware AI Scheduling Agent

FastAPI + LangGraph backend, React/Vite frontend. iCloud CalDAV + iOS Reminders integration. Per-day energy curve drives task placement; LLM (Claude via LangChain) handles task decomposition and chat-based adjustments.

## Daily startup

Two terminals, both run from the project root:

```bash
# Terminal 1 — backend (FastAPI on :8000)
.venv/bin/uvicorn main:app --reload

# Terminal 2 — frontend (Vite on :5173)
cd frontend && pnpm dev
```

Open <http://localhost:5173>.

**Stopping the backend.** If it's in the foreground, `Ctrl+C`. If you lost the terminal, kill whatever holds port 8000:

```bash
lsof -ti:8000 | xargs kill        # graceful
lsof -ti:8000 | xargs kill -9     # force, if it won't die
```

(`--reload` runs a parent + worker, so you'll usually see two PIDs — the command above kills both.) The frontend stops the same way on port 5173.

### Swift frontend (alternative native macOS client)

An experimental native SwiftUI sidebar lives in `cal_swift_frontend/`. It talks to the **same** backend on :8000, so start the backend first.

```bash
# Start (from cal_swift_frontend/)
cd cal_swift_frontend && swift run ScheduleAgentApp

# Stop
pkill -f ScheduleAgentApp

# Check if it's running
pgrep -fl ScheduleAgentApp
```

First `swift build`/`swift run` populates a local `.build/` (~270 MB of compile artifacts — not source, and should stay out of git).

## First-time setup

```bash
# Python backend
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env: set at least ANTHROPIC_API_KEY + CALDAV_* credentials

# Frontend
cd frontend
pnpm install
```

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
- `tests/` — pytest suite (79 tests, fully offline)

## Phase status

Current: **Phase A complete** — LangGraph migration + task_kind dimension + ChatAnthropic structured output everywhere. See [docs/phase3-plan.md](docs/phase3-plan.md) for the full roadmap (Phases A–E).

## Optional LangSmith tracing

Add to `.env` for free trace visualization of every graph run and Claude call:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=dayflow
```

Restart uvicorn — traces appear at <https://smith.langchain.com>.
