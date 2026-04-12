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

# Keywords that indicate a task is a quick action (< 10 min)
_INSTANT_KEYWORDS = [
    # English
    'pay ', 'send ', 'email ', 'call ', 'text ', 'reply ', 'submit ', 'sign ',
    'buy ', 'order ', 'book ', 'reserve ', 'print ', 'upload ', 'download ',
    'transfer ', 'wire ', 'renew ', 'confirm ',
    # Chinese
    '交', '付款', '还款', '支付', '发送', '提交', '预约', '购买', '订购',
    '签名', '打印', '上传', '转账', '汇款', '续费', '确认',
]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a task planning assistant. Output ONLY valid JSON — no explanation, no markdown fences.
All text fields (e.g. "title") must be written in {language}.

Analyze each task and decompose it into appropriately-sized subtasks. Follow these guidelines:

INSTANT DETECTION — output a single subtask with is_instant=true if the task is a quick action
(pay a bill, send an email, make a call, submit a form, buy something, etc.) regardless of
estimated_hours. Instant subtasks get estimated_minutes=5.

TIME ESTIMATION by task type:
- Quick action / reminder: 5 min (is_instant=true)
- Simple errand or reply: 15–30 min, 1 subtask
- Course reading / short quiz: 30–60 min, 1 subtask
- Problem set / assignment: 90–180 min → split into 2–3 subtasks of ≤90 min each
- Course project milestone: 3–8 hours → 3–6 subtasks with phase labels
- Long-term project (15+ hrs): spread across multiple days, each subtask 60–90 min

PHASE LABELS — add phase_label to each subtask when a task has 3+ subtasks spanning
multiple sessions. Format: "Phase 1 · Research", "Phase 2 · Implementation", "Phase 3 · Review"

CONSTRAINTS:
- Max subtask: 90 min (deep), 60 min (medium), 45 min (light)
- Respect deadlines: prefer today for tasks due today or overdue
- High-priority tasks must have at least one subtask assigned to today
- Output subtasks ordered by urgency: overdue/today first, then earliest deadline, then priority

Return a flat JSON array — no other text:
[
  {{
    "parent_id": "<task id>",
    "title": "<subtask title in {language}>",
    "estimated_minutes": <integer>,
    "cognitive_load": "deep" | "medium" | "light",
    "suggested_date": "<YYYY-MM-DD>" | null,
    "phase_label": "<phase label>" | null,
    "is_instant": true | false
  }}
]
"""


def _is_instant_task(task: Task) -> bool:
    """Fast heuristic for clearly instant tasks — avoids sending to Claude."""
    if task.is_instant:
        return True
    if task.estimated_hours <= 0.1:  # ≤ 6 min
        return True
    title_lower = task.title.lower()
    return any(kw in title_lower for kw in _INSTANT_KEYWORDS)


async def rank_and_decompose(
    tasks: list[Task],
    target_date: date,
    language: Language = Language.en,
) -> list[Subtask]:
    """
    Call Claude to rank and decompose tasks. Validates output with Pydantic.
    Instant tasks are short-circuited without a Claude call.
    Falls back to a simple heuristic split if the API call fails.
    """
    if not tasks:
        return []

    # Separate instant tasks — pass through without Claude
    instant_tasks = [t for t in tasks if _is_instant_task(t)]
    regular_tasks = [t for t in tasks if not _is_instant_task(t)]

    subtasks: list[Subtask] = []

    # Instant tasks → single pass-through subtask each
    for t in instant_tasks:
        subtasks.append(Subtask(
            parent_id=t.id,
            title=t.title,
            cognitive_load=CognitiveLoad.light,
            estimated_minutes=5,
            suggested_date=t.deadline or target_date,
            is_instant=True,
        ))

    if not regular_tasks:
        return subtasks

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)

    # Pre-sort by urgency before sending to Claude
    sorted_tasks = sorted(
        regular_tasks,
        key=lambda t: (
            t.deadline or date.max,
            t.priority.value,
        ),
    )

    task_payload = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "priority": t.priority.value,
            "cognitive_load": t.cognitive_load.value,
            "estimated_hours": t.estimated_hours,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "is_uncertain": t.is_uncertain,
        }
        for t in sorted_tasks
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

    # Build deadline/priority lookup for post-sort safety net
    deadline_by_id = {t.id: (t.deadline or date.max) for t in regular_tasks}
    priority_by_id = {t.id: t.priority.value for t in regular_tasks}

    try:
        raw_list = json.loads(raw_text)
        claude_subtasks = [Subtask.model_validate(item) for item in raw_list]
        # Safety net: re-sort by parent task urgency
        claude_subtasks.sort(key=lambda s: (
            deadline_by_id.get(s.parent_id, date.max),
            priority_by_id.get(s.parent_id, "medium"),
        ))
        subtasks.extend(claude_subtasks)
    except (json.JSONDecodeError, ValidationError, KeyError):
        subtasks.extend(_heuristic_decompose(sorted_tasks, target_date))

    return subtasks


def _heuristic_decompose(tasks: list[Task], target_date: date) -> list[Subtask]:
    """Simple fallback decomposer when Claude call fails."""
    subtasks: list[Subtask] = []
    for task in sorted(tasks, key=lambda t: (t.deadline or date.max, t.priority.value)):
        total_minutes = int(task.estimated_hours * 60)
        chunk = _MAX_SUBTASK_MINUTES[task.cognitive_load]
        idx = 1
        remaining = total_minutes
        n_chunks = max(1, (total_minutes + chunk - 1) // chunk)
        while remaining > 0:
            size = min(remaining, chunk)
            phase = f"Phase {idx}" if n_chunks >= 3 else None
            subtasks.append(
                Subtask(
                    parent_id=task.id,
                    title=f"{task.title} (part {idx})" if n_chunks > 1 else task.title,
                    cognitive_load=task.cognitive_load,
                    estimated_minutes=size,
                    suggested_date=target_date,
                    phase_label=phase,
                )
            )
            remaining -= size
            idx += 1
    return subtasks
