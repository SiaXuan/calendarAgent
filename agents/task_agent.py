"""
Task Agent — uses Claude API to rank and decompose tasks into subtasks.
Always validates Claude's JSON output with Pydantic before use.
"""
import json
import os
from datetime import date

import anthropic
from pydantic import ValidationError

from models.task import CognitiveLoad, Subtask, Task
from models.user import Language

_MAX_SUBTASK_MINUTES = {
    CognitiveLoad.deep: 90,
    CognitiveLoad.medium: 60,
    CognitiveLoad.light: 45,
}

_SYSTEM_PROMPT_TEMPLATE = """\
You are a task planning assistant. Output ONLY valid JSON — no explanation, no markdown fences.
All text fields (e.g. "title") must be written in {language}.

Given a list of tasks, decompose each one into focused subtasks that can each be completed in a single \
sitting. Apply these rules:
- Max subtask size: 90 min (deep work), 60 min (medium), 45 min (light)
- Prefer subtasks completable in one sitting
- Respect deadlines when suggesting dates (prefer today for high-priority tasks with today's deadline)
- High-priority tasks should have at least one subtask assigned to today

Return a flat JSON array of objects with this exact shape:
[
  {{
    "parent_id": "<task id>",
    "title": "<subtask title>",
    "estimated_minutes": <integer>,
    "cognitive_load": "deep" | "medium" | "light",
    "suggested_date": "<YYYY-MM-DD>" | null
  }}
]
"""


async def rank_and_decompose(
    tasks: list[Task],
    target_date: date,
    language: Language = Language.en,
) -> list[Subtask]:
    """
    Call Claude to rank and decompose tasks. Validates output with Pydantic.
    Falls back to a simple heuristic split if the API call fails.
    """
    if not tasks:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)

    task_payload = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "priority": t.priority.value,
            "cognitive_load": t.cognitive_load.value,
            "estimated_hours": t.estimated_hours,
            "deadline": t.deadline.isoformat() if t.deadline else None,
        }
        for t in tasks
    ]

    user_message = (
        f"Today's date: {target_date.isoformat()}\n\n"
        f"Tasks:\n{json.dumps(task_payload, indent=2)}"
    )

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = message.content[0].text.strip()

    # Strip accidental markdown fences
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        raw_list = json.loads(raw_text)
        subtasks = [Subtask.model_validate(item) for item in raw_list]
    except (json.JSONDecodeError, ValidationError, KeyError):
        # Fallback: simple rule-based decomposition (always English titles)
        subtasks = _heuristic_decompose(tasks, target_date)

    return subtasks


def _heuristic_decompose(tasks: list[Task], target_date: date) -> list[Subtask]:
    """Simple fallback decomposer when Claude call fails."""
    subtasks: list[Subtask] = []
    for task in sorted(tasks, key=lambda t: (t.priority.value, t.deadline or date.max)):
        total_minutes = int(task.estimated_hours * 60)
        chunk = _MAX_SUBTASK_MINUTES[task.cognitive_load]
        idx = 1
        remaining = total_minutes
        while remaining > 0:
            size = min(remaining, chunk)
            subtasks.append(
                Subtask(
                    parent_id=task.id,
                    title=f"{task.title} (part {idx})" if total_minutes > chunk else task.title,
                    cognitive_load=task.cognitive_load,
                    estimated_minutes=size,
                    suggested_date=target_date,
                )
            )
            remaining -= size
            idx += 1
    return subtasks
