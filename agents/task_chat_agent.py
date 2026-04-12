"""
Task Chat Agent — manages per-task AI conversations for uncertain or complex tasks.
The user can discuss scope, breakdown, and time estimates with the model.
When the user confirms a plan, returns decomposed subtasks that replace Claude's default decomposition.
"""
import json
import os
from datetime import date

import anthropic
from pydantic import BaseModel, ValidationError

from models.task import CognitiveLoad, Subtask, Task
from models.user import Language


class ChatMessage(BaseModel):
    role: str    # "user" | "assistant"
    content: str


class TaskChatResult(BaseModel):
    reply: str
    decomposed_subtasks: list[Subtask] | None = None


_SYSTEM_TEMPLATE = """\
You are a smart task planning assistant. The user wants to clarify and break down a specific task.

Your goal:
1. Ask questions to understand scope, constraints, and available time
2. Help estimate realistic time (consider: is it a quick reminder, a course assignment, a project?)
3. Propose a concrete decomposition with phase labels and time estimates
4. When the user agrees, output the final plan as a JSON block

RULES:
- Be conversational; don't dump the JSON until the user confirms
- When confirmed, output a "---" separator then a JSON array with this shape:
  [{{"parent_id": "{task_id}", "title": "...", "estimated_minutes": <int>,
    "cognitive_load": "deep"|"medium"|"light", "suggested_date": "<YYYY-MM-DD>"|null,
    "phase_label": "..."|null, "is_instant": false}}]
- All user-facing text in: {language}

Task context:
  id: {task_id}
  title: {title}
  deadline: {deadline}
  estimated_hours: {estimated_hours}
  description: {description}
  today: {today}
"""


async def chat(
    task: Task,
    messages: list[ChatMessage],
    target_date: date,
    language: Language = Language.en,
) -> TaskChatResult:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    system = _SYSTEM_TEMPLATE.format(
        task_id=task.id,
        title=task.title,
        deadline=task.deadline.isoformat() if task.deadline else "none",
        estimated_hours=task.estimated_hours,
        description=task.description or "none",
        today=target_date.isoformat(),
        language=language.value,
    )

    api_messages = [{"role": m.role, "content": m.content} for m in messages]

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=api_messages,
    )

    reply_text = response.content[0].text.strip()

    # Parse decomposed subtasks from JSON block after "---" separator
    decomposed: list[Subtask] | None = None
    if "---" in reply_text:
        parts = reply_text.rsplit("---", 1)
        reply_clean = parts[0].strip()
        json_part = parts[1].strip()
        # Strip markdown fences
        if json_part.startswith("```"):
            json_part = json_part.split("```")[1]
            if json_part.startswith("json"):
                json_part = json_part[4:]
            json_part = json_part.strip()
        try:
            raw = json.loads(json_part)
            decomposed = [Subtask.model_validate(item) for item in raw]
            reply_text = reply_clean
        except (json.JSONDecodeError, ValidationError):
            pass  # Keep full reply_text if JSON parse fails

    return TaskChatResult(reply=reply_text, decomposed_subtasks=decomposed)
