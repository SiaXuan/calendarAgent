"""Tests for agents/task_agent.py — LLM mocked, no network."""
import pytest

from agents.task_agent import _LLMSubtask, _LLMSubtaskList, rank_and_decompose
from models.task import CognitiveLoad, TaskKind
from models.user import Language


pytestmark = pytest.mark.asyncio


async def test_empty_input_returns_empty(mock_sonnet, sample_date):
    """No tasks → no subtasks. LLM should not be called."""
    result = await rank_and_decompose([], sample_date, Language.en)
    assert result == []
    assert mock_sonnet._structured.calls == []


async def test_instant_task_skips_llm(mock_sonnet, sample_instant_task, sample_date):
    """is_instant tasks pass through directly — no LLM call."""
    result = await rank_and_decompose([sample_instant_task], sample_date, Language.en)
    assert len(result) == 1
    assert result[0].is_instant is True
    assert result[0].title == "Pay electricity bill"
    assert result[0].estimated_minutes == 5
    assert result[0].task_kind == TaskKind.admin
    # Critically: LLM was not called
    assert mock_sonnet._structured.calls == []


async def test_regular_task_calls_llm_with_structured_output(
    mock_sonnet, sample_task, sample_date,
):
    """Regular task → LLM returns structured subtasks, mapped through."""
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id="task_001",
            title="Design energy curve",
            estimated_minutes=60,
            cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
            suggested_date=sample_date,
        ),
        _LLMSubtask(
            parent_id="task_001",
            title="Implement and unit-test",
            estimated_minutes=60,
            cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
            suggested_date=sample_date,
        ),
    ]))

    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    assert len(result) == 2
    assert result[0].title == "Design energy curve"
    assert result[0].task_kind == TaskKind.analytical
    assert result[0].is_instant is False
    # Deadline was patched from parent
    assert result[0].deadline == sample_date


async def test_llm_short_minutes_floored_to_25(mock_sonnet, sample_task, sample_date):
    """If LLM returns < 25 min for a non-instant task, floor it to ≥ 25."""
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id="task_001",
            title="Quick step",
            estimated_minutes=5,   # too short
            cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))
    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    assert result[0].estimated_minutes >= 25
    # sample_task has 2.0 estimated_hours → expected floor = max(25, 2*60) = 120
    assert result[0].estimated_minutes == 120


async def test_llm_failure_falls_back_to_heuristic(mock_sonnet, sample_task, sample_date):
    """If the structured-output call raises, the heuristic splitter takes over."""
    mock_sonnet.set_structured_error(ValueError("LLM API down"))

    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    # sample_task is 2h deep → heuristic chunks of 90min → 2 subtasks
    assert len(result) >= 1
    assert all(s.parent_id == "task_001" for s in result)
    assert all(s.task_kind == TaskKind.analytical for s in result)


async def test_task_kind_defaults_when_llm_omits(mock_sonnet, sample_task, sample_date):
    """If LLM somehow returns a subtask without task_kind, default is analytical."""
    # _LLMSubtask has TaskKind.analytical as the default
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id="task_001",
            title="Work block",
            estimated_minutes=60,
            cognitive_load=CognitiveLoad.deep,
            # task_kind intentionally omitted
        ),
    ]))
    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    assert result[0].task_kind == TaskKind.analytical


async def test_split_subtasks_get_distinct_titles(mock_sonnet, sample_task, sample_date):
    """
    Same-titled subtasks of one parent must be disambiguated to (1/N)(2/N) —
    both for UX and because block_key = task_id::title would otherwise collide.
    """
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(parent_id="task_001", title="完成 LeetCode 刷题 ×2",
                    estimated_minutes=25, cognitive_load=CognitiveLoad.deep,
                    task_kind=TaskKind.analytical),
        _LLMSubtask(parent_id="task_001", title="完成 LeetCode 刷题 ×2",
                    estimated_minutes=25, cognitive_load=CognitiveLoad.deep,
                    task_kind=TaskKind.analytical),
    ]))
    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    titles = [s.title for s in result]
    # distinct now
    assert len(set(titles)) == 2
    assert any("1/2" in t for t in titles)
    assert any("2/2" in t for t in titles)


async def test_single_subtask_title_unchanged(mock_sonnet, sample_task, sample_date):
    """A non-duplicated title is left alone (no spurious (1/1))."""
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(parent_id="task_001", title="独一无二的任务",
                    estimated_minutes=60, cognitive_load=CognitiveLoad.deep,
                    task_kind=TaskKind.analytical),
    ]))
    result = await rank_and_decompose([sample_task], sample_date, Language.en)
    assert result[0].title == "独一无二的任务"


async def test_mixed_instant_and_regular(
    mock_sonnet, sample_task, sample_instant_task, sample_date,
):
    """Instant and regular tasks coexist: instants pass through, LLM only sees regular."""
    mock_sonnet.set_structured_response(_LLMSubtaskList(subtasks=[
        _LLMSubtask(
            parent_id="task_001",
            title="Real work block",
            estimated_minutes=60,
            cognitive_load=CognitiveLoad.deep,
            task_kind=TaskKind.analytical,
        ),
    ]))
    result = await rank_and_decompose(
        [sample_instant_task, sample_task], sample_date, Language.en,
    )
    # 1 instant + 1 regular subtask
    instant_subs = [s for s in result if s.is_instant]
    regular_subs = [s for s in result if not s.is_instant]
    assert len(instant_subs) == 1
    assert len(regular_subs) == 1
    # Verify LLM only saw the regular task
    assert len(mock_sonnet._structured.calls) == 1
    user_msg = mock_sonnet._structured.calls[0][1]["content"]
    assert "task_001" in user_msg
    assert "task_instant_001" not in user_msg
