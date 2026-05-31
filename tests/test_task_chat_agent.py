"""Tests for agents/task_chat_agent.py — LLM mocked, no network."""
import json

import pytest

from agents.task_chat_agent import ChatMessage, chat
from models.user import Language


pytestmark = pytest.mark.asyncio


async def test_conversational_reply_no_plan(mock_sonnet, sample_task, sample_date):
    """No '---' separator → reply is plain prose, no decomposition extracted."""
    mock_sonnet.set_plain_response(
        "Can you tell me more about the scope? Is this an hour of work or more?"
    )
    result = await chat(
        sample_task,
        [ChatMessage(role="user", content="I need help planning this")],
        sample_date,
        Language.en,
    )
    assert result.decomposed_subtasks is None
    assert "scope" in result.reply.lower()


async def test_confirmed_plan_emits_decomposed_subtasks(
    mock_sonnet, sample_task, sample_date,
):
    """Reply ends with '---' + JSON array → parsed into Subtasks."""
    plan_json = json.dumps([
        {
            "parent_id": sample_task.id,
            "title": "Step 1: research existing implementations",
            "estimated_minutes": 60,
            "cognitive_load": "deep",
            "task_kind": "analytical",
            "suggested_date": sample_date.isoformat(),
            "phase_label": "Phase 1 · Research",
            "is_instant": False,
        },
        {
            "parent_id": sample_task.id,
            "title": "Step 2: implement",
            "estimated_minutes": 90,
            "cognitive_load": "deep",
            "task_kind": "analytical",
            "suggested_date": sample_date.isoformat(),
            "phase_label": "Phase 2 · Build",
            "is_instant": False,
        },
    ])
    mock_sonnet.set_plain_response(
        f"Great, here is the final plan.\n\n---\n{plan_json}"
    )

    result = await chat(
        sample_task,
        [ChatMessage(role="user", content="Looks good, lock it in")],
        sample_date,
        Language.en,
    )
    assert result.decomposed_subtasks is not None
    assert len(result.decomposed_subtasks) == 2
    assert result.decomposed_subtasks[0].title.startswith("Step 1")
    assert result.decomposed_subtasks[0].phase_label == "Phase 1 · Research"
    # The "---" + JSON should be stripped from the reply
    assert "---" not in result.reply
    assert plan_json not in result.reply


async def test_markdown_fenced_json_block_is_stripped(
    mock_sonnet, sample_task, sample_date,
):
    """LLM sometimes wraps JSON in ```json fences; we should still parse it."""
    plan = [{
        "parent_id": sample_task.id,
        "title": "Just one block",
        "estimated_minutes": 60,
        "cognitive_load": "deep",
        "task_kind": "analytical",
        "suggested_date": sample_date.isoformat(),
        "phase_label": None,
        "is_instant": False,
    }]
    mock_sonnet.set_plain_response(
        f"Confirmed!\n---\n```json\n{json.dumps(plan)}\n```"
    )
    result = await chat(
        sample_task,
        [ChatMessage(role="user", content="ok")],
        sample_date,
        Language.en,
    )
    assert result.decomposed_subtasks is not None
    assert len(result.decomposed_subtasks) == 1


async def test_malformed_json_keeps_full_reply(mock_sonnet, sample_task, sample_date):
    """If JSON after '---' fails to parse, reply text is preserved verbatim."""
    mock_sonnet.set_plain_response("Here is my plan\n---\nthis is not json")
    result = await chat(
        sample_task,
        [ChatMessage(role="user", content="ok")],
        sample_date,
        Language.en,
    )
    assert result.decomposed_subtasks is None
    assert "this is not json" in result.reply
