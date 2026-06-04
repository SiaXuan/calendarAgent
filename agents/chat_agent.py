"""
Chat Agent — translates natural language adjustments into AdjustmentParams.

Phase A migration: switched from raw `anthropic.AsyncAnthropic` + manual JSON
parsing to `ChatAnthropic.with_structured_output(...)` for free Pydantic
validation + LangSmith tracing.
"""
import json

from pydantic import BaseModel

from agents.llm import sonnet
from models.schedule import DaySchedule
from models.user import Language


class AdjustmentParams(BaseModel):
    energy_threshold_modifier: float = 0.0   # e.g. -0.2 means lower all thresholds by 0.2
    remove_blocks_after_hour: int | None = None   # e.g. 13 → clear afternoon
    reschedule_block_title: str | None = None     # title of block to reschedule
    reschedule_to_hour: int | None = None         # target hour for reschedule
    add_task_title: str | None = None             # e.g. "Gym session"
    add_task_load: str | None = None              # "light" | "medium" | "deep"
    add_task_minutes: int | None = None
    raw_intent: str = ""


_SYSTEM_PROMPT_TEMPLATE = """\
You are a scheduling assistant. Given a user message and their current schedule, \
describe what adjustment to make.
All text fields (e.g. "add_task_title", "raw_intent") must be written in {language}.

Fill only the fields that apply; leave the rest at their default (0.0 / null / empty string).
- energy_threshold_modifier: float, e.g. -0.2 if user is tired
- remove_blocks_after_hour: 24h hour if clearing afternoon/evening
- reschedule_block_title + reschedule_to_hour: rescheduling a specific block
- add_task_title + add_task_load + add_task_minutes: adding a new task
- raw_intent: one-line summary of what you understood
"""


async def handle_message(
    message: str,
    current_schedule: DaySchedule,
    language: Language = Language.en,
    memory_context: list[str] | None = None,
) -> AdjustmentParams:
    """Memory-aware adjustment parsing.

    `memory_context` (Phase C.3) — confidence-filtered user-pattern bullets
    appended to the system prompt so requests like "lighter morning" are
    interpreted with knowledge of what *this user* considers light/heavy.
    """
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)
    if memory_context:
        bullets = "\n".join(f"  - {b}" for b in memory_context)
        system_prompt += (
            "\n\nKNOWN USER PREFERENCES (factor these into your interpretation):\n"
            f"{bullets}"
        )

    schedule_summary = {
        "date": current_schedule.date.isoformat(),
        "health_summary": current_schedule.health_summary,
        "blocks": [
            {
                "title": b.title,
                "start": b.start.strftime("%H:%M"),
                "end": b.end.strftime("%H:%M"),
                "type": b.block_type.value,
            }
            for b in current_schedule.blocks
        ],
    }

    user_content = (
        f"User message: {message}\n\n"
        f"Current schedule:\n{json.dumps(schedule_summary, indent=2)}"
    )

    try:
        structured_llm = sonnet.with_structured_output(AdjustmentParams)
        result: AdjustmentParams = await structured_llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ])
        # Ensure raw_intent is set even if the model omitted it
        if not result.raw_intent:
            result = result.model_copy(update={"raw_intent": message})
        return result
    except Exception:
        return AdjustmentParams(raw_intent=message)
