"""
Task Agent — uses Claude (via langchain-anthropic) to rank and decompose tasks.

Phase A migration: switched from raw `anthropic.AsyncAnthropic` + ad-hoc JSON
parsing to `ChatAnthropic.with_structured_output(...)`. This gives us:
  * Pydantic-validated output for free (no manual `json.loads` + ValidationError)
  * LangSmith trace integration when LANGSMITH_TRACING=true
  * Easy model swap via agents/llm.py

The instant-task short-circuit and heuristic fallback remain unchanged.
"""
import json
from collections import Counter
from datetime import date

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from agents.llm import sonnet
from models.task import CognitiveLoad, Subtask, Task, TaskKind
from models.user import Language

_MAX_SUBTASK_MINUTES = {
    CognitiveLoad.deep: 90,
    CognitiveLoad.medium: 60,
    CognitiveLoad.light: 45,
}


# ─── Structured-output schema for the LLM ────────────────────────────────────
# We wrap the list in an object because `with_structured_output` expects a
# single top-level schema, not a bare array.

class _LLMSubtask(BaseModel):
    parent_id: str
    title: str
    estimated_minutes: int
    cognitive_load: CognitiveLoad
    task_kind: TaskKind = TaskKind.analytical
    suggested_date: date | None = None
    phase_label: str | None = None
    is_instant: bool = False


class _LLMSubtaskList(BaseModel):
    subtasks: list[_LLMSubtask]


_SYSTEM_PROMPT_TEMPLATE = """\
You are a task planning assistant. All text fields (e.g. "title") must be written in {language}.

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

TASK_KIND — orthogonal to cognitive_load; classifies *what kind of cognitive process* the
subtask needs, which we'll use later to place it at the optimal time of day:
- analytical: focused attention, problem-solving, structured analysis
              (coding, debugging, math problems, structured writing, reading papers with intent)
- insight:    creative, novel-association, open-ended exploration
              (brainstorming, design, outlining new ideas, drafting from scratch, research framing)
- admin:      procedural, low-focus, mostly mechanical
              (emails, filing, scheduling, organising, formatting, simple data entry)
When unsure, default to "analytical".

CONSTRAINTS:
- Max subtask: 90 min (deep), 60 min (medium), 45 min (light)
- Respect deadlines: prefer today for tasks due today or overdue
- High-priority tasks must have at least one subtask assigned to today
- Output subtasks ordered by urgency: overdue/today first, then earliest deadline, then priority
"""


def _disambiguate_titles(subtasks: list[Subtask]) -> list[Subtask]:
    """
    Append （i/N) to subtasks that share a title within the same parent task.

    Two purposes:
      1. UX — split subtasks ("做2道LeetCode" → 2 sessions) read as distinct,
         not a confusing duplicate.
      2. Correctness — block_key is "{task_id}::{title}"; identical titles
         collide, which would break per-block pins / sync / feedback that key
         off block_key. Distinct titles give distinct keys.
    """
    totals = Counter((s.parent_id, s.title) for s in subtasks)
    seen: dict[tuple, int] = {}
    out: list[Subtask] = []
    for s in subtasks:
        key = (s.parent_id, s.title)
        if totals[key] > 1:
            seen[key] = seen.get(key, 0) + 1
            out.append(s.model_copy(update={"title": f"{s.title}（{seen[key]}/{totals[key]}）"}))
        else:
            out.append(s)
    return out


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
    memory_context: list[str] | None = None,
) -> list[Subtask]:
    """
    Call Claude to rank and decompose tasks. Validates output with Pydantic.
    Instant tasks are short-circuited without a Claude call.
    Falls back to a simple heuristic split if the API call fails.

    `memory_context` (Phase C.3) is a list of confidence-filtered user-pattern
    bullets — schedule_prefs memories above PROD_CONFIDENCE_FLOOR. They get
    appended to the system prompt as a "KNOWN USER PREFERENCES" section so the
    LLM honours learned habits when picking task_kind / suggested_date.
    """
    if not tasks:
        return []

    # Separate instant tasks — pass through without Claude
    instant_tasks = [t for t in tasks if _is_instant_task(t)]
    regular_tasks = [t for t in tasks if not _is_instant_task(t)]

    subtasks: list[Subtask] = []

    # Instant tasks → single pass-through subtask each. task_kind defaults to
    # admin because these are by definition procedural / low-focus actions.
    for t in instant_tasks:
        subtasks.append(Subtask(
            parent_id=t.id,
            title=t.title,
            cognitive_load=CognitiveLoad.light,
            task_kind=TaskKind.admin,
            estimated_minutes=5,
            suggested_date=t.deadline or target_date,
            deadline=t.deadline,
            due_datetime=t.deadline_dt,   # preserve full time for InstantCard display
            is_instant=True,
        ))

    if not regular_tasks:
        return _disambiguate_titles(subtasks)

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

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)
    if memory_context:
        bullets = "\n".join(f"  - {b}" for b in memory_context)
        system_prompt += (
            "\n\nKNOWN USER PREFERENCES (learned from past accept/dismiss signals):\n"
            f"{bullets}\n"
            "When picking suggested_date and task_kind, honour these preferences "
            "unless an explicit constraint (deadline, priority) overrides them."
        )
    user_message = (
        f"Today's date: {target_date.isoformat()}\n\n"
        f"Tasks:\n{json.dumps(task_payload, indent=2)}"
    )

    # Build deadline/priority lookup for post-sort safety net
    deadline_by_id = {t.id: t.deadline for t in regular_tasks}
    deadline_sort_key = {t.id: (t.deadline or date.max) for t in regular_tasks}
    priority_by_id = {t.id: t.priority.value for t in regular_tasks}
    parent_hours = {t.id: t.estimated_hours for t in regular_tasks}

    try:
        structured_llm = sonnet.with_structured_output(_LLMSubtaskList)
        result: _LLMSubtaskList = await structured_llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        # Map LLM subtasks → real Subtask, patching deadline + safety floors.
        # Claude sometimes marks subtasks as instant (5 min) because titles start
        # with action verbs ("完成X", "提交X") — these are regular work steps,
        # not quick actions, so force is_instant=False and floor the duration.
        claude_subtasks: list[Subtask] = []
        for item in result.subtasks:
            minutes = item.estimated_minutes if item.estimated_minutes >= 25 \
                else max(25, int(parent_hours.get(item.parent_id, 0.5) * 60))
            claude_subtasks.append(Subtask(
                parent_id=item.parent_id,
                title=item.title,
                cognitive_load=item.cognitive_load,
                task_kind=item.task_kind,
                estimated_minutes=minutes,
                suggested_date=item.suggested_date,
                deadline=deadline_by_id.get(item.parent_id),
                phase_label=item.phase_label,
                is_instant=False,
            ))

        # Safety net: re-sort by parent task urgency
        claude_subtasks.sort(key=lambda s: (
            deadline_sort_key.get(s.parent_id, date.max),
            priority_by_id.get(s.parent_id, "medium"),
        ))
        subtasks.extend(claude_subtasks)
    except Exception:
        # Any LLM-side failure (network, parser, validation, provider quota,
        # rate-limit, etc.) → fall back to the deterministic heuristic so the
        # rest of the schedule pipeline doesn't crash.
        subtasks.extend(_heuristic_decompose(sorted_tasks, target_date))

    return _disambiguate_titles(subtasks)


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
                    task_kind=task.task_kind,
                    estimated_minutes=size,
                    suggested_date=target_date,
                    deadline=task.deadline,
                    phase_label=phase,
                )
            )
            remaining -= size
            idx += 1
    return subtasks
