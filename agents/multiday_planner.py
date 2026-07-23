"""
Multi-day planner (Phase 4, Step 1.6).

Only tasks whose deadline is inside the scheduling window are planned here (far
deadlines stay off the radar — see agents/nodes.SCHEDULE_HORIZON_DAYS). For each
in-window task the reasoning LLM reads its source context, works out the concrete
*phases/steps* the work needs (not identical time slices), sizes each step, and
lays them across the days up to the deadline so the task gets done gradually and,
where it fits, wrapped up (收口) rather than scattered. A deterministic greedy
fill is the fallback when the LLM is unavailable.

The backend owns this purely as computation over `project_task_store` nodes +
per-day capacities; it never touches the calendar.
"""
import json
import logging
from datetime import date, timedelta

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from agents.llm import sonnet
from models.planning import DayCapacity, PlannedChunk
from models.task import Task
from models.user import Language

_log = logging.getLogger("dayflow")

# A step is one uninterrupted sitting — keep it humane, don't slice into slivers.
_MIN_STEP_MIN = 30
_MAX_STEP_MIN = 120


class _LLMStep(BaseModel):
    task_id: str
    step_title: str       # concrete: what to actually do this session
    date: date
    minutes: int


class _LLMPlan(BaseModel):
    steps: list[_LLMStep]


_SYSTEM_PROMPT = """\
You are a study/work planner. For each task, think about what it ACTUALLY
involves — use its title and `context` (the source document snippet) to infer the
real sub-goals and knowledge points — then break it into a short sequence of
concrete PHASES/STEPS and lay those steps across the available days. All text you
emit (step_title) must be in {language}.

How to think (this is the point — don't just slice a task into equal blocks):
- Identify the task's natural phases. e.g. an assignment → 读材料/理解知识点 →
  动手实现/做题 → 调试/检查 → 写报告并提交. A study task → 过一遍概念 → 练习 → 复盘.
  Name each step by WHAT gets done, so no two steps share a generic name.
- Give each step a realistic duration ({min_step}–{max_step} min). A step is ONE
  uninterrupted sitting — never split a single step across two days.
- Lay steps in order across the days, earliest first, all on/before the deadline.
- Prefer to WRAP a task up (收口) — if it fits in a day (or its remaining work
  does), keep it together rather than smearing one task thinly over many days.
- Respect each day's free_minutes: everything you place on a day must sum ≤ its
  capacity. Lighter-capacity days (a class day) get less.
- Don't invent busywork to fill capacity; it's fine to leave days lighter.

Return steps: each {{task_id (exactly as given), step_title, date (YYYY-MM-DD),
minutes}}.
"""


def build_capacities(
    today: date, horizon_end: date, work_minutes_per_day: int,
    fixed_minutes_by_date: dict[date, int] | None = None,
) -> list[DayCapacity]:
    """Free minutes per day in [today, horizon_end]. `fixed_minutes_by_date` is
    that day's committed time (meetings/classes) subtracted from the work window;
    absent → the whole work window is free."""
    fixed = fixed_minutes_by_date or {}
    out: list[DayCapacity] = []
    d = today
    while d <= horizon_end:
        out.append(DayCapacity(date=d, free_minutes=max(0, work_minutes_per_day - fixed.get(d, 0))))
        d += timedelta(days=1)
    return out


def _node_minutes(t: Task) -> int:
    return max(_MIN_STEP_MIN, int(round((t.estimated_hours or 1.0) * 60)))


def greedy_plan(
    nodes: list[Task], capacities: list[DayCapacity], today: date,
) -> list[PlannedChunk]:
    """Deterministic fallback (used only when the LLM is unavailable): can't infer
    real phases, so it emits numbered sessions of one task, earliest-deadline
    first, filling each day up to capacity, never past the deadline."""
    remaining = {c.date: c.free_minutes for c in capacities}
    days = [c.date for c in capacities]
    chunks: list[PlannedChunk] = []
    for t in sorted(nodes, key=lambda n: (n.deadline or date.max, n.priority.value)):
        left = _node_minutes(t)
        n_sessions = max(1, -(-left // _MAX_STEP_MIN))   # ceil
        idx = 0
        deadline = t.deadline or (days[-1] if days else today)
        for d in days:
            if left <= 0 or d > deadline:
                break
            avail = remaining.get(d, 0)
            if avail < _MIN_STEP_MIN:
                continue
            take = min(left, avail, _MAX_STEP_MIN)
            idx += 1
            chunks.append(PlannedChunk(
                project_id=t.project_id or "", task_id=t.id, task_title=t.title,
                title=f"{t.title}（第 {idx}/{n_sessions} 段）",
                date=d, minutes=take, cognitive_load=t.cognitive_load))
            remaining[d] = avail - take
            left -= take
    return chunks


async def plan_project_work(
    nodes: list[Task], capacities: list[DayCapacity], today: date,
    language: Language = Language.en,
) -> list[PlannedChunk]:
    """Plan in-window nodes into phased steps across days. LLM first; on any
    failure (or an allocation that overflows a day / lands past a deadline) falls
    back to the deterministic greedy fill so a plan always comes out."""
    if not nodes:
        return []

    by_id = {t.id: t for t in nodes}
    node_payload = [
        {
            "task_id": t.id,
            "title": t.title,
            "total_minutes": _node_minutes(t),
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "priority": t.priority.value,
            "cognitive_load": t.cognitive_load.value,
            "context": t.source_excerpt or t.description,
        }
        for t in nodes
    ]
    cap_payload = [{"date": c.date.isoformat(), "free_minutes": c.free_minutes}
                   for c in capacities]
    system = _SYSTEM_PROMPT.format(
        language=language.value, min_step=_MIN_STEP_MIN, max_step=_MAX_STEP_MIN)
    user = (
        f"Today: {today.isoformat()}\n\n"
        f"Days (free capacity):\n{json.dumps(cap_payload, indent=2)}\n\n"
        f"Tasks:\n{json.dumps(node_payload, ensure_ascii=False, indent=2)}"
    )

    try:
        structured = sonnet.with_structured_output(_LLMPlan)
        result: _LLMPlan = await structured.ainvoke([
            {"role": "system", "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": user},
        ])
        chunks = [
            PlannedChunk(
                project_id=by_id[s.task_id].project_id or "",
                task_id=s.task_id,
                task_title=by_id[s.task_id].title,
                title=s.step_title.strip() or by_id[s.task_id].title,
                date=s.date,
                minutes=max(_MIN_STEP_MIN, min(_MAX_STEP_MIN, s.minutes)),
                cognitive_load=by_id[s.task_id].cognitive_load,
            )
            for s in result.steps if s.task_id in by_id
        ]
        if chunks and _valid(chunks, capacities, by_id):
            return chunks
        _log.info("multiday_planner: LLM plan failed validation → greedy fallback")
    except (OutputParserException, ValidationError, KeyError) as exc:
        _log.warning("multiday_planner: LLM plan error (%s) → greedy fallback", exc)
    except Exception:
        _log.exception("multiday_planner: unexpected LLM error → greedy fallback")

    return greedy_plan(nodes, capacities, today)


def _valid(chunks: list[PlannedChunk], capacities: list[DayCapacity],
           by_id: dict[str, Task]) -> bool:
    """Reject a plan that overflows any day's capacity or lands work past a
    deadline — the greedy fallback is safer than a bad LLM allocation."""
    cap = {c.date: c.free_minutes for c in capacities}
    per_day: dict[date, int] = {}
    for c in chunks:
        if c.date not in cap:
            return False
        node = by_id.get(c.task_id)
        if node and node.deadline and c.date > node.deadline:
            return False
        per_day[c.date] = per_day.get(c.date, 0) + c.minutes
    return all(per_day.get(d, 0) <= cap[d] for d in per_day)
