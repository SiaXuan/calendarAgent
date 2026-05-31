"""Tests for agents/chat_agent.py — LLM mocked, no network."""
from datetime import datetime

import pytest

from agents.chat_agent import AdjustmentParams, handle_message
from models.schedule import BlockType, DaySchedule, TimeBlock
from models.user import Language


pytestmark = pytest.mark.asyncio


def _fake_schedule(target_date):
    return DaySchedule(
        date=target_date,
        energy_curve=[0.5] * 24,
        blocks=[TimeBlock(
            start=datetime(target_date.year, target_date.month, target_date.day, 9, 0),
            end=datetime(target_date.year, target_date.month, target_date.day, 10, 0),
            block_type=BlockType.scheduled,
            title="Work block",
        )],
        unscheduled=[],
        health_summary="OK",
    )


async def test_happy_path_returns_structured_params(mock_sonnet, sample_date):
    mock_sonnet.set_structured_response(AdjustmentParams(
        remove_blocks_after_hour=15,
        raw_intent="clear afternoon",
    ))
    result = await handle_message(
        "clear my afternoon", _fake_schedule(sample_date), Language.en,
    )
    assert result.remove_blocks_after_hour == 15
    assert result.raw_intent == "clear afternoon"


async def test_raw_intent_fills_in_when_llm_omits(mock_sonnet, sample_date):
    """If the LLM returns params with empty raw_intent, we backfill with the message."""
    mock_sonnet.set_structured_response(AdjustmentParams(
        energy_threshold_modifier=-0.2,
        raw_intent="",   # intentionally empty
    ))
    result = await handle_message(
        "I'm tired", _fake_schedule(sample_date), Language.en,
    )
    assert result.raw_intent == "I'm tired"
    assert result.energy_threshold_modifier == -0.2


async def test_llm_error_returns_empty_params_with_raw_intent(mock_sonnet, sample_date):
    """If the LLM call blows up, we still return a usable AdjustmentParams."""
    mock_sonnet.set_structured_error(RuntimeError("API down"))

    result = await handle_message(
        "do something", _fake_schedule(sample_date), Language.en,
    )
    assert result.raw_intent == "do something"
    assert result.energy_threshold_modifier == 0.0
    assert result.remove_blocks_after_hour is None


async def test_schedule_summary_is_passed_to_llm(mock_sonnet, sample_date):
    """Verify the LLM call includes the current schedule context."""
    mock_sonnet.set_structured_response(AdjustmentParams(raw_intent="x"))

    await handle_message("test", _fake_schedule(sample_date), Language.en)

    assert len(mock_sonnet._structured.calls) == 1
    user_content = mock_sonnet._structured.calls[0][1]["content"]
    # Schedule date + block title should be in the prompt context
    assert sample_date.isoformat() in user_content
    assert "Work block" in user_content
