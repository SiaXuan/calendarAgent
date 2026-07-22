"""
Plan extraction agent (Phase 4 Step 2).

Takes the plain text of a document and asks Claude to decide whether it's a
plan and, if so, pull out project meta + candidate tasks as a structured
`ExtractedPlan`. The static rules block is cached (cache_control:ephemeral,
same pattern as task_agent) since only {language} varies per call.

Intent gating (is_plan / confidence / empty candidates) is applied by the
caller (api/projects.py::import_plan), not here — this agent only extracts.
"""
from agents.llm import sonnet
from models.plan_import import ExtractedPlan
from models.user import Language

_SYSTEM_PROMPT_TEMPLATE = """\
You extract an actionable project plan from a document. All human-readable text
fields you produce (titles, descriptions, rejection_reason) must be written in {language}.

You are given the plain text of a document a user wants to turn into a schedule:
a course syllabus, a PRD, a project roadmap, a schedule table, or a freeform goal.

Your job:
1. Decide if this is actually a plan we can schedule (is_plan) and how sure you are
   (confidence, 0..1). A plan describes work items — assignments, milestones, phases,
   deliverables, readings — usually with dates or an ordering.
   NOT plans: invoices, receipts, marketing pages, articles, resumes, random notes.
   If it is not a plan, set is_plan=false and write a short, friendly rejection_reason
   in {language} explaining what you saw instead, and leave candidate_tasks empty.
2. Classify doc_kind (syllabus / prd / schedule_table / roadmap / freeform_goal / other).
3. Set has_explicit_schedule=true if the doc states concrete dates/weeks for items.
4. Fill project_meta when the doc implies them: title, description, deadline, start_date.
5. Extract candidate_tasks — one per distinct work item. For each:
   - title: concise, actionable, in {language}.
   - description: optional extra context.
   - explicit_date / explicit_deadline: ONLY if a concrete calendar date is stated or
     unambiguously derivable. Do NOT invent dates. Relative markers like "Week 3" with
     no anchor date → leave dates null and set needs_decomposition=true.
   - week_index: the 1-based term week from a schedule table's "Week" column, if present.
   - due_weekday: the day of week the item is due, as an integer 0=Monday..6=Sunday,
     if the doc states one (e.g. "due Monday 9AM" → 0). Else null.
   - estimated_hours: rough effort if inferable, else null.
   - priority (high/medium/low) and cognitive_load (deep/medium/light): if inferable.
   - phase_label: e.g. "Phase 1 · Research" when the doc groups work into phases.
   - needs_decomposition: true if this item is a chunk of work that should be broken
     into sessions later; false only for a single trivial action on a single date.
   - source_excerpt: the short snippet of the document this item came from.
6. If (and only if) a USER INSTRUCTION is given below, fill `adjustment` from it —
   this is how the user reshapes the dates (e.g. reusing an old syllabus for a new term):
   - target_year: the year to move the plan to, if stated ("move to 2027" → 2027).
   - term_start_date: the week-1 anchor date, ONLY if the instruction states a concrete
     date for it; otherwise null.
   - due_weekday: an overridden due weekday (0=Mon..6=Sun), if the instruction changes it
     ("class moved to Wednesday" → 2).
   - shift_weeks: a whole-plan nudge in weeks ("push everything back a week" → 1).
   CRITICAL: do NOT compute shifted calendar dates yourself and do NOT edit
   explicit_date to the new year — only fill `adjustment`. The backend does the
   date arithmetic deterministically. If no instruction is given, leave adjustment empty.

Extract only what the document supports. Do not pad the plan with invented tasks.
"""


async def extract_plan(
    text: str, language: Language = Language.en, instruction: str | None = None,
) -> ExtractedPlan:
    """Extract a structured plan from document text. An optional user instruction
    (natural language) is parsed into `adjustment` for date shifting. Raises on
    LLM/validation failure (the caller maps that to a 502)."""
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)
    user_content = f"Document text:\n\n{text}"
    if instruction and instruction.strip():
        user_content += (
            f"\n\n---\nUSER INSTRUCTION (use it to fill `adjustment`, and honour any "
            f"weekday/term changes it states):\n{instruction.strip()}"
        )
    structured_llm = sonnet.with_structured_output(ExtractedPlan)
    result: ExtractedPlan = await structured_llm.ainvoke([
        {"role": "system", "content": [
            {"type": "text", "text": system_prompt,
             "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": user_content},
    ])
    return result
