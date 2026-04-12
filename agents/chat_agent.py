"""
Chat Agent — translates natural language adjustments into AdjustmentParams.
Uses Claude API.
"""
import json
import os

import anthropic
from pydantic import BaseModel

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
return ONLY valid JSON describing what adjustment to make. No explanation.
All text fields (e.g. "add_task_title", "raw_intent") must be written in {language}.

Return an object with these optional keys (omit keys that don't apply):
{{
  "energy_threshold_modifier": <float, e.g. -0.2 if user is tired>,
  "remove_blocks_after_hour": <int, 24h hour if clearing afternoon/evening>,
  "reschedule_block_title": <string>,
  "reschedule_to_hour": <int>,
  "add_task_title": <string>,
  "add_task_load": "light" | "medium" | "deep",
  "add_task_minutes": <int>,
  "raw_intent": <string, one-line summary of what you understood>
}}
"""


async def handle_message(
    message: str,
    current_schedule: DaySchedule,
    language: Language = Language.en,
) -> AdjustmentParams:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)

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

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        return AdjustmentParams.model_validate(data)
    except Exception:
        return AdjustmentParams(raw_intent=message)
