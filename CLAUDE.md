# Health-Aware AI Scheduling Agent

## Project Structure

- `main.py` — FastAPI entry point, mounts all routers
- `agents/` — All LLM + logic agents
- `api/` — FastAPI route handlers
- `models/` — Pydantic data models
- `integrations/` — External service clients (Phase 2)
- `tests/` — pytest test suite + mock data

## Running Locally

```bash
# one-time setup — use venv to isolate the fast-moving langchain stack
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# Set ANTHROPIC_API_KEY at minimum
.venv/bin/uvicorn main:app --reload
```

See [docs/deployment.md](docs/deployment.md) for the local vs cloud config split.

## Key Conventions

- All agent methods are `async`
- Parallel agent execution via `asyncio.gather`
- All Claude API calls output JSON only; always validated with Pydantic
- Health and Calendar agent results are cached per date (in-memory dict)
- Only Scheduler Agent re-runs on chat adjustments
- No hardcoded credentials — always use `.env`
- Datetimes stored in user's local timezone
- **Always pass `user_prefs.language` to any function that produces user-facing text.** The orchestrator reads `get_current_prefs().language` and threads it into `health_agent.get_health_summary`, `task_agent.rank_and_decompose`, and `chat_agent.handle_message`. Never hard-code a language assumption inside an agent.

## Internationalization (i18n)

**Supported locales:** `en`, `zh-CN`, `zh-TW`, `ja` — defined in `models/user.py::Language`.

**Backend:** Language preference is stored in `UserPreferences` (in-memory, Phase 1). Read/write via `GET /preferences` and `PATCH /preferences`. The orchestrator reads it once per request via `get_current_prefs()`.

- `task_agent` and `chat_agent` inject language into their Claude system prompts so all generated text fields are returned in the correct locale.
- `health_agent.get_health_summary` is async: English uses rule-based logic (no LLM); other locales make a small Claude translation call, falling back to English on error.

**Frontend (not implemented — documentation only):**
The frontend uses `react-i18next` with locale files under `src/locales/{lang}.json`. Keys mirror all static UI strings (nav labels, button text, card labels). Language preference is fetched from `GET /preferences` on app load and stored in React context. No display strings are hard-coded in components.

## Phase Status

- **Phase 1** ✅ Core pipeline with mock data (no real API integrations)
- **Phase 2** ✅ Real CalDAV (iCloud) + iOS Reminders integration; manual sleep input
- **Phase 3** ✅ Chat agent + calendar write-back + per-task planning chat
- **Phase 4 (in progress)** 🔄 Migration to LangGraph + LangMem long-term memory
  + multi-source health adapter + research-driven health rules
  + user-attached MCP extension interface.
  See [docs/phase3-plan.md](docs/phase3-plan.md) for the full roadmap.
