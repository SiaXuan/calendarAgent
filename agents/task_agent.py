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

CRITICAL — DO NOT mark tasks as instant unless they are trivially quick (< 5 min, zero thinking):
- is_instant=true ONLY for: pay a bill, click submit on a COMPLETED form, send a short email,
  make a quick phone call, buy something online. These require NO preparation or thinking.
- is_instant=false for EVERYTHING ELSE, especially:
  * "X due" (e.g., "CV due", "ML due") = the X ASSIGNMENT is due. This requires hours of real work
    (coding, writing, implementing). Always false, always estimate ≥ 60 min.
  * "X test", "X exam", "X assessment" = multi-hour work requiring preparation + execution.
  * "agent phase3", "project milestone", "implement X" = software/engineering work, false.
  * Any task with estimated_hours > 0.1 → never instant.
  All tasks passed to you have estimated_hours > 0.1, so set is_instant=false for ALL of them.

TIME ESTIMATION — use estimated_hours as your anchor (it was set by the user):
- Honour estimated_hours: if a task has estimated_hours=2.0, total subtask minutes ≈ 120 min
- Simple errand or reply: 15–30 min, 1 subtask
- Course reading / short quiz: 30–60 min, 1 subtask
- Problem set / assignment: 90–180 min → 2–3 subtasks of ≤90 min each
- Course project milestone: 3–8 hours → 3–6 subtasks with phase labels
- Long-term project (15+ hrs): spread across multiple days, each subtask 60–90 min
- MINIMUM subtask size: 25 min (never output estimated_minutes < 25 for a non-instant task)

PHASE LABELS — add phase_label to each subtask when a task has 3+ subtasks spanning
multiple sessions. Format: "Phase 1 · Research", "Phase 2 · Implementation", "Phase 3 · Review"

COGNITIVE LOAD — assign independently per subtask, do NOT just copy the parent task's value:
- deep:   sustained focus + original thinking required
          (coding, writing from scratch, solving problems, exam prep, implementing algorithms,
           debugging, ML/CV/CS assignments, research, paper writing)
- medium: moderate attention, less creative effort
          (grading, reviewing work, reading with notes, planning, replying to complex messages,
           revising a draft, data entry with judgement)
- light:  minimal mental effort, mostly mechanical
          (simple admin, scheduling, quick check-ins, filing, watching a video, re-reading notes)

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
    "estimated_minutes": <integer ≥ 25>,
    "cognitive_load": "deep" | "medium" | "light",
    "suggested_date": "<YYYY-MM-DD>" | null,
    "phase_label": "<phase label>" | null,
    "is_instant": false
  }}
]
"""


def _is_instant_task(task: Task) -> bool:
    """
    Check if a task should bypass Claude and go straight to the instant path.
    Trust task.is_instant from the task store (set by api/tasks.py with exclusions).
    Do NOT re-apply keyword heuristics here — that would override the exclusion list
    and misclassify things like '提交 CV Assessment2' as instant.
    """
    if task.is_instant:
        return True
    if task.estimated_hours <= 0.1:  # ≤ 6 min
        return True
    return False


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
            deadline=t.deadline,
            due_datetime=t.deadline_dt,   # preserve full time for InstantCard display
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
    deadline_by_id = {t.id: t.deadline for t in regular_tasks}
    deadline_sort_key = {t.id: (t.deadline or date.max) for t in regular_tasks}
    priority_by_id = {t.id: t.priority.value for t in regular_tasks}

    try:
        raw_list = json.loads(raw_text)
        claude_subtasks = [Subtask.model_validate(item) for item in raw_list]
        # Patch deadline, force is_instant=False, and enforce minimum time.
        # Claude sometimes marks subtasks as instant (5 min) because titles start with
        # action verbs ("完成X", "提交X") — these are regular work steps, not quick actions.
        parent_hours = {t.id: t.estimated_hours for t in regular_tasks}
        claude_subtasks = [
            s.model_copy(update={
                "deadline": deadline_by_id.get(s.parent_id),
                "is_instant": False,
                # If Claude gave an instant-sized time (< 25 min) for a real task,
                # floor it to max(25, parent_estimated_hours * 60) so blocks have
                # actual work duration and don't vanish in the timeline.
                "estimated_minutes": s.estimated_minutes if s.estimated_minutes >= 25
                    else max(25, int(parent_hours.get(s.parent_id, 0.5) * 60)),
            })
            for s in claude_subtasks
        ]
        # Safety net: re-sort by parent task urgency
        claude_subtasks.sort(key=lambda s: (
            deadline_sort_key.get(s.parent_id, date.max),
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
                    deadline=task.deadline,
                    phase_label=phase,
                )
            )
            remaining -= size
            idx += 1
    return subtasks
