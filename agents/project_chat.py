"""
Per-project planning conversation (Phase 4).

A project is one long chat with full memory: the user talks to refine the plan
("把 assignment3 拆细一点", "这周太满，挪到下周"), and the assistant both replies
and, when asked to change something, returns the revised task list. The
orchestration (history, applying the revision, persistence) lives in
agents/project_service.chat_about_plan; this module is just the LLM call.
"""
from typing import Literal

from pydantic import BaseModel

from agents.llm import sonnet
from models.plan_import import CandidateTask
from models.user import Language


class TaskProgress(BaseModel):
    """The user reporting how far along a task is, in conversation ("作业1 交了" /
    "论文还没写完"). `done` marks the whole task finished (its planned work stops
    being scheduled/carried); `in_progress` keeps it flowing (the default)."""
    task_title: str
    status: Literal["done", "in_progress"]


class PlanChatResult(BaseModel):
    reply: str
    # When the plan changes, the COMPLETE new task list (every task that should
    # exist after the change). null when the turn didn't change the plan.
    tasks: list[CandidateTask] | None = None
    # When the user reports progress on existing tasks (finished / still going).
    # null when the turn reports no progress.
    progress: list[TaskProgress] | None = None


_SYSTEM_PROMPT = """\
You are the planning assistant for ONE project: "{name}". You hold the full
conversation memory and the project's current plan (below). This is a
conversation — the plan gets shaped gradually across turns. Reply in {language}.

- Put your natural-language answer in `reply`.
- When the user asks to CHANGE the plan (add / remove / reschedule / split /
  merge tasks, shift dates, adjust workload), OR pastes/attaches a document or
  image that clearly describes work to schedule (syllabus, TOC, reading/chapter
  list, checklist, goals...), return the COMPLETE new task list in `tasks` —
  every task that should exist afterwards, not just the delta. Keep a task's
  `title` stable if it's the same item so its progress/reminders survive. Set
  `explicit_deadline` (YYYY-MM-DD) when a date is known, `estimated_hours` for
  workload, `source_excerpt` to the relevant bit of the source.
- NEVER hard-reject. If what the user sent is unclear, off-topic, or you're not
  sure it's schedulable, DON'T force it into tasks — leave `tasks` null and reply
  by saying what you think it is and asking what they'd like to do with it
  (e.g. "这看起来像一份 X，你是想把它排成计划，还是…?").
- When the user is only asking a question or you're clarifying, leave `tasks`
  null (no plan change).
- When the user REPORTS PROGRESS on existing work ("作业1 交了 / 做完了",
  "论文那部分还没写完 / 还在弄"), fill `progress` with `{task_title, status}`
  using the matching task's exact title: `done` when they finished the whole
  task, `in_progress` when it's still ongoing. `progress` is independent of
  `tasks` — a pure progress report doesn't change the plan (leave `tasks` null).
- Ground everything in the project's source material; don't invent scope.
"""


async def converse(
    project_name: str, current_plan: str, history: list[dict], message: str,
    language: Language = Language.en,
    doc_text: str | None = None, image: tuple[bytes, str] | None = None,
) -> PlanChatResult:
    """One planning turn: (current plan + full history + new message, optionally
    with an attached document's text or an image) → reply + optional revised task
    list. Raises on LLM/validation failure (caller maps to a friendly error)."""
    import base64

    system = _SYSTEM_PROMPT.format(name=project_name, language=language.value)
    messages: list[dict] = [
        {"role": "system", "content": f"{system}\n\nCurrent plan:\n{current_plan}"}
    ]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]

    text = message or "（见附件）"
    if doc_text:
        text += f"\n\n---\n（附来的文档内容）\n{doc_text}"
    if image is not None:
        b64 = base64.standard_b64encode(image[0]).decode()
        messages.append({"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{image[1]};base64,{b64}"}},
        ]})
    else:
        messages.append({"role": "user", "content": text})

    structured = sonnet.with_structured_output(PlanChatResult)
    return await structured.ainvoke(messages)
