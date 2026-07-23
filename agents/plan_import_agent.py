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
   deliverables, readings — usually with dates or an ordering. Dates are NOT
   required: a table of contents, a chapter/reading list, a checklist, or a bare
   list of topics the user wants to get through IS a plan — each item becomes a
   task (e.g. "阅读第 3 章"). If the user says they want to work through / finish /
   study it (even without dates), treat it as a plan and extract the items.
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
6. If the input contains a user instruction about reshaping the dates — either in a
   dedicated USER INSTRUCTION section below, OR stated inline within the document text
   itself (e.g. the pasted text opens with "this is a 2025 syllabus, move it to 2027,
   homework still due Mondays") — fill `adjustment` from it and do NOT turn that note
   into a task. This is how the user reshapes dates (e.g. reusing an old syllabus):
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


async def extract_plan_from_image(
    image_bytes: bytes, mime: str, language: Language = Language.en,
    instruction: str | None = None,
) -> ExtractedPlan:
    """Same extraction, but from an image (pasted screenshot / photo of a
    syllabus, whiteboard, schedule table, …) via Claude vision — no OCR. Same
    rules block + structured output as the text path; the model reads the image
    directly. Raises on LLM/validation failure (caller maps to 502)."""
    import base64

    b64 = base64.standard_b64encode(image_bytes).decode()
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language=language.value)
    text = ("The image below is a plan (a screenshot or photo of a syllabus / PRD / "
            "schedule / a written list of goals). Read it and extract the plan.")
    if instruction and instruction.strip():
        text += (
            f"\n\n---\nUSER INSTRUCTION (use it to fill `adjustment`, and honour any "
            f"weekday/term changes it states):\n{instruction.strip()}"
        )
    structured_llm = sonnet.with_structured_output(ExtractedPlan)
    result: ExtractedPlan = await structured_llm.ainvoke([
        {"role": "system", "content": [
            {"type": "text", "text": system_prompt,
             "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ])
    return result
