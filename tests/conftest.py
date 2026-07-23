"""
Shared pytest fixtures for the dayflow test suite.

Goal: every test runs without real network, real LLM, or real CalDAV calls.
Each fixture is opt-in — tests that don't need it pay no cost.
"""
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.health import HealthSnapshot, SleepData
from models.schedule import BlockType, FreeWindow, TimeBlock
from models.task import CognitiveLoad, Priority, Subtask, Task, TaskKind


# ─── Reset persistence between tests ────────────────────────────────────────

@pytest.fixture
def clean_stores(tmp_path, monkeypatch):
    """
    Wipe in-memory stores before/after the test AND redirect storage's JSON
    file paths to a tmp dir. The redirect is critical: API tests that POST to
    /tasks call save_task_store(), which would otherwise persist test data into
    the real data/task_store.json and leak across runs (each test run added a
    new "Test task" forever).
    """
    import storage
    from agents import nodes

    # Redirect file paths so saves go to tmp_path, not the real data/ dir
    monkeypatch.setattr(storage, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "_HEALTH_FILE", tmp_path / "health_store.json")
    monkeypatch.setattr(storage, "_TASKS_FILE", tmp_path / "task_store.json")
    monkeypatch.setattr(storage, "_MEMORY_FILE", tmp_path / "memory_store.json")
    monkeypatch.setattr(storage, "_SCHEDULE_FILE", tmp_path / "schedule_store.json")
    monkeypatch.setattr(storage, "_PROJECT_FILE", tmp_path / "project_store.json")
    monkeypatch.setattr(storage, "_COMPLETION_FILE", tmp_path / "completion_store.json")
    monkeypatch.setattr(storage, "_PROJECT_PLAN_FILE", tmp_path / "project_plan_store.json")
    monkeypatch.setattr(storage, "_PROJECT_TASK_FILE", tmp_path / "project_task_store.json")

    def _wipe():
        storage.health_store.clear()
        storage.task_store.clear()
        storage.project_task_store.clear()
        storage.schedule_store.clear()
        storage.subtask_overrides.clear()
        storage.subtask_pins.clear()
        storage.memory_store.clear()
        storage.observation_log.clear()
        storage.schedule_version.clear()
        storage.pending_proposals.clear()
        storage.agent_run_log.clear()
        storage.chat_sessions.clear()
        storage.project_store.clear()
        storage.completion_store.clear()
        storage.project_plan_store.clear()
        nodes._health_cache.clear()
        nodes._calendar_cache.clear()

    _wipe()
    yield
    _wipe()


# ─── Mock external integrations ─────────────────────────────────────────────

@pytest.fixture
def mock_caldav(monkeypatch):
    """
    Replace calendar_agent.fetch_fixed_blocks with a deterministic stub:
      - one fixed block 13:00–14:00 (lunch meeting)
      - two free windows around it (work_start–13 and 14–work_end)
    """
    async def fake_fetch(target_date, work_start, work_end):
        fixed = [TimeBlock(
            start=datetime(target_date.year, target_date.month, target_date.day, 13, 0),
            end=datetime(target_date.year, target_date.month, target_date.day, 14, 0),
            block_type=BlockType.fixed,
            title="Lunch meeting",
        )]
        free = [
            FreeWindow(
                start_hour=work_start, end_hour=13,
                duration_minutes=(13 - work_start) * 60,
            ),
            FreeWindow(
                start_hour=14, end_hour=work_end,
                duration_minutes=(work_end - 14) * 60,
            ),
        ]
        return fixed, free

    monkeypatch.setattr("agents.calendar_agent.fetch_fixed_blocks", fake_fetch)


@pytest.fixture
def mock_reminders_sync(monkeypatch):
    """Bypass the AppleScript-driven iOS Reminders sync (touches real OS)."""
    async def fake_sync():
        return {"added": 0, "updated": 0, "skipped": 0, "tasks": []}

    # patch BOTH possible import sites — nodes.sync_reminders_if_due
    # internally imports api.tasks.do_sync_reminders at call time.
    monkeypatch.setattr("api.tasks.do_sync_reminders", fake_sync, raising=False)


# ─── Mock ChatAnthropic ─────────────────────────────────────────────────────

class _MockStructuredRunnable:
    """Stand-in for `sonnet.with_structured_output(Schema)`."""

    def __init__(self):
        self._response = None
        self._raise = None
        self.calls: list[list] = []

    def set_response(self, value):
        self._response = value
        self._raise = None

    def set_error(self, exc: Exception):
        self._raise = exc

    async def ainvoke(self, messages, *args, **kwargs):
        self.calls.append(messages)
        if self._raise is not None:
            raise self._raise
        return self._response


class _MockChatAnthropic:
    """Stand-in for the `sonnet` / `haiku` ChatAnthropic instances."""

    def __init__(self):
        self._structured = _MockStructuredRunnable()
        self._plain_response = None
        self._plain_raise = None
        self.plain_calls: list[list] = []

    def with_structured_output(self, schema):
        return self._structured

    # For task_chat_agent / health_agent.translate which call .ainvoke directly
    async def ainvoke(self, messages, *args, **kwargs):
        self.plain_calls.append(messages)
        if self._plain_raise is not None:
            raise self._plain_raise
        # Return an object with .content (mimicking AIMessage)
        response = MagicMock()
        response.content = self._plain_response or ""
        return response

    # ── helpers tests use to set up canned responses ────────────────────────
    def set_structured_response(self, value):
        self._structured.set_response(value)

    def set_structured_error(self, exc):
        self._structured.set_error(exc)

    def set_plain_response(self, text: str):
        self._plain_response = text
        self._plain_raise = None

    def set_plain_error(self, exc: Exception):
        self._plain_raise = exc


@pytest.fixture
def mock_sonnet(monkeypatch):
    """
    Replace `sonnet` everywhere it's imported. Returns the mock so tests can
    configure its response with `mock_sonnet.set_structured_response(...)`.
    """
    mock = _MockChatAnthropic()
    for path in [
        "agents.task_agent.sonnet",
        "agents.chat_agent.sonnet",
        "agents.task_chat_agent.sonnet",
        "agents.llm.sonnet",
    ]:
        monkeypatch.setattr(path, mock)
    return mock


@pytest.fixture
def mock_haiku(monkeypatch):
    """Replace `haiku` everywhere it's imported."""
    mock = _MockChatAnthropic()
    for path in [
        "agents.health_agent.haiku",
        "agents.llm.haiku",
    ]:
        monkeypatch.setattr(path, mock)
    return mock


# ─── Sample data builders ───────────────────────────────────────────────────

@pytest.fixture
def sample_date():
    return date(2026, 5, 15)


@pytest.fixture
def sample_snapshot(sample_date):
    return HealthSnapshot(
        date=sample_date,
        sleep=SleepData(
            duration_hours=7.5,
            sleep_start=datetime.combine(sample_date - timedelta(days=1), datetime.min.time()).replace(hour=23, minute=0),
            sleep_end=datetime.combine(sample_date, datetime.min.time()).replace(hour=6, minute=30),
        ),
        resting_heart_rate=58,
        hrv=45.0,
        steps=8200,
        active_minutes=38,
    )


@pytest.fixture
def sample_task(sample_date):
    return Task(
        id="task_001",
        title="Implement energy curve algorithm",
        priority=Priority.high,
        cognitive_load=CognitiveLoad.deep,
        task_kind=TaskKind.analytical,
        estimated_hours=2.0,
        deadline=sample_date,
        source="manual",
    )


@pytest.fixture
def sample_instant_task(sample_date):
    """An instant task that should bypass the LLM."""
    return Task(
        id="task_instant_001",
        title="Pay electricity bill",
        priority=Priority.medium,
        cognitive_load=CognitiveLoad.light,
        task_kind=TaskKind.admin,
        estimated_hours=0.05,   # 3 min → instant
        deadline=sample_date,
        source="manual",
        is_instant=True,
    )
