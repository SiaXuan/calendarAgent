"""
Per-project planning conversation (Phase 4).

A project is one long chat with full memory: the user talks to refine the plan
("把 assignment3 拆细一点", "这周太满，挪到下周"), and the assistant both replies
and, when asked to change something, returns the revised task list. The
orchestration (history, applying the revision, persistence) lives in
agents/project_service.chat_about_plan; this module is just the LLM call.
"""
import re
from datetime import date
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
  `estimated_hours` for workload, `source_excerpt` to the relevant bit of the
  source.
- Dates — NEVER compute a date yourself; dates are worked out by the system.
  * `explicit_deadline` (YYYY-MM-DD): only for a full date, year included, that
    appears verbatim in an attached document or the user's message.
  * A date given without a year ("9/14", "11月3号"): `deadline_change` with
    `month` + `day`.
  * A change described in words: `deadline_change` —
    `shift_days` moves it relative to its current date (推迟两天 = 2,
    提前一周 = -7, 挪到下周 = 7); `weekday` (0=Mon..6=Sun) with `weeks_from_now`
    (0 = this week, 1 = next week) for "下周五"; `weekday` alone for "周五"
    meaning the next one.
  * A task whose date isn't changing: leave `explicit_deadline` and
    `deadline_change` both empty — its current date is kept.
- In `reply`, never write a date. Wherever you'd state a task's date, write
  [[deadline:<the task's exact title>]] and the real date is filled in.
- NEVER hard-reject. If what the user sent is unclear, off-topic, or you're not
  sure it's schedulable, DON'T force it into tasks — leave `tasks` null and reply
  by saying what you think it is and asking what they'd like to do with it
  (e.g. "这看起来像一份 X，你是想把它排成计划，还是…?").
- When the user is only asking a question or you're clarifying, leave `tasks`
  null (no plan change).
- When the user REPORTS PROGRESS on existing work ("作业1 交了 / 做完了",
  "论文那部分还没写完 / 还在弄"), fill `progress` with `{{task_title, status}}`
  using the matching task's exact title: `done` when they finished the whole
  task, `in_progress` when it's still ongoing. `progress` is independent of
  `tasks` — a pure progress report doesn't change the plan (leave `tasks` null).
- Ground everything in the project's source material; don't invent scope.
"""


_WEEKDAY_NAMES = {
    "zh-CN": "一二三四五六日",
    "zh-TW": "一二三四五六日",
    "ja": "月火水木金土日",
}
_EN_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_NO_DATE = {"zh-CN": "（未定日期）", "zh-TW": "（未定日期）", "ja": "（日付未定）", "en": "(no date)"}
_UNKNOWN = {"zh-CN": "（日期见计划）", "zh-TW": "（日期見計畫）", "ja": "（日付は計画を参照）",
            "en": "(see the plan)"}

_PLACEHOLDER = re.compile(r"\[\[deadline:(.+?)\]\]")


def format_date(d: date, language: Language, today: date | None = None) -> str:
    """A date the way a reply in `language` would say it; year only when it isn't this one."""
    lang = getattr(language, "value", language)
    today = today or date.today()
    if lang == "en":
        out = f"{_EN_WEEKDAYS[d.weekday()]}, {_EN_MONTHS[d.month - 1]} {d.day}"
        return out + (f", {d.year}" if d.year != today.year else "")
    names = _WEEKDAY_NAMES.get(lang, _WEEKDAY_NAMES["zh-CN"])
    prefix = f"{d.year}年" if d.year != today.year else ""
    sep = "（週" if lang == "zh-TW" else ("（" if lang == "ja" else "（周")
    return f"{prefix}{d.month}月{d.day}日{sep}{names[d.weekday()]}）"


def fill_deadline_placeholders(reply: str, tasks, language: Language,
                               today: date | None = None) -> str:
    """Replace [[deadline:<title>]] with that task's saved deadline.

    The reply is written before the dates exist — the model describes the
    change, code computes it — so the model leaves a placeholder and this fills
    it from the plan as stored. Titles are matched exactly, then through the
    same normalisation re-import uses, so "1. 作业3" still finds "作业3".
    """
    from agents.plan_reconcile import normalize_title

    lang = getattr(language, "value", language)
    by_title = {t.title: t for t in tasks}
    by_norm = {normalize_title(t.title): t for t in tasks}

    def sub(m: re.Match) -> str:
        title = m.group(1).strip()
        task = by_title.get(title) or by_norm.get(normalize_title(title))
        if task is None:
            return _UNKNOWN.get(lang, _UNKNOWN["en"])
        if task.deadline is None:
            return _NO_DATE.get(lang, _NO_DATE["en"])
        return format_date(task.deadline, language, today)

    return _PLACEHOLDER.sub(sub, reply)


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
