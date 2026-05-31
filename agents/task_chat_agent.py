"""
Task Chat Agent — manages per-task AI conversations for uncertain or complex tasks.

The user can discuss scope, breakdown, and time estimates with the model.
When the user confirms a plan, the bot emits prose + a "---" separator + JSON,
which we parse into Subtasks that override the default decomposition.

Phase A migration: switched from raw `anthropic.AsyncAnthropic` to
`ChatAnthropic` (LangChain). The "---"-then-JSON pattern stays because the bot
is supposed to *converse* before committing — we don't want a structured-output
constraint forcing it to emit JSON on every turn.
"""
import json
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agents.llm import sonnet
from models.task import Subtask, Task
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
    "cognitive_load": "deep"|"medium"|"light",
    "task_kind": "analytical"|"insight"|"admin",
    "suggested_date": "<YYYY-MM-DD>"|null,
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
    system = _SYSTEM_TEMPLATE.format(
        task_id=task.id,
        title=task.title,
        deadline=task.deadline.isoformat() if task.deadline else "none",
        estimated_hours=task.estimated_hours,
        description=task.description or "none",
        today=target_date.isoformat(),
        language=language.value,
    )

    lc_messages = [SystemMessage(content=system)]
    for m in messages:
        if m.role == "user":
            lc_messages.append(HumanMessage(content=m.content))
        else:
            lc_messages.append(AIMessage(content=m.content))

    response = await sonnet.ainvoke(lc_messages)
    reply_text = (response.content or "").strip() if isinstance(response.content, str) \
        else "".join(part.get("text", "") for part in response.content if isinstance(part, dict)).strip()

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
