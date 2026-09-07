"""
Re-import reconcile (Phase 4, Step 2 second half).

Importing a REVISED version of an already-imported document must not rebuild the
project. Before this module, a second import minted fresh uuids for every
candidate and appended them next to the old tasks: duplicated titles, and — worse
— every `block_key` ("{task_id}::{title}") changed, so all completion records
orphaned. Work you'd actually finished showed up as pending, progress reset, and
the reminder diff happily re-created nodes that were already done.

So the job here is narrow: match this import's candidates against the project's
existing tasks and REUSE their ids, so identity survives a revision. Everything
downstream (`replan_project` → `reconcile_reminders` → EventKit) already diffs
correctly on content hashes and skips done nodes; it just needs a task set whose
ids mean the same thing they did last time.

Matching is deterministic and title-based, on purpose:

- Titles are what the block_key is built from, so a title match is exactly the
  case where identity can be preserved for free.
- Normalisation absorbs the edits a revised document actually makes to a title
  it didn't mean to rename — whitespace, full/half-width punctuation, casing,
  and leading list numbering ("3. " / "Week 3 — ").
- Anything the normaliser doesn't catch falls through as remove + add, which is
  the honest answer: a genuinely reworded node IS a different node, and pretending
  otherwise would silently transplant completion history onto different work.

Completion records are the one thing that never gets dropped on the floor: a
matched node whose title changed has its record migrated to the new key
(`plan_diff.completion_migrations`), and a node that disappears from the document
but was already DONE is kept rather than deleted — finishing something and then
revising the syllabus doesn't un-finish it.
"""
import re
import unicodedata

from pydantic import BaseModel

from models.project import CompletionStatus
from models.task import Task


class PlanDiff(BaseModel):
    """What a re-import would do, as a reviewable summary.

    `dry_run` returns this so the user confirms before anything is deleted —
    re-import is destructive (it drops nodes the revised document no longer
    contains), so it goes through the same confirm gate as any other
    irreversible action.
    """
    matched: list[dict] = []      # {task_id, old_title, new_title, renamed}
    added: list[str] = []         # titles new in this document
    removed: list[dict] = []      # {task_id, title, was_done} — dropped from the plan
    kept_done: list[dict] = []    # {task_id, title} — gone from the doc but already done
    completion_migrations: list[dict] = []   # {old_key, new_key}

    @property
    def is_first_import(self) -> bool:
        return not (self.matched or self.removed or self.kept_done)


# Leading list markers a revised doc loves to renumber: "3.", "3)", "(3)",
# "Week 3 -", "第3周:", "- ", "* ".
_LEADING_MARKER = re.compile(
    r"^\s*(?:[-*•]|\(?\d+[.)、]|week\s*\d+|第\s*\d+\s*[周週课課章节節]?)\s*[-—–:：.]?\s*",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Fold a title down to what identifies it, ignoring cosmetic edits.

    NFKC first so full-width punctuation and half-width forms compare equal
    (a doc round-tripped through a different editor otherwise looks fully
    rewritten). Then strip leading numbering, drop punctuation and whitespace,
    and casefold.
    """
    folded = unicodedata.normalize("NFKC", title).casefold()
    folded = _LEADING_MARKER.sub("", folded)
    # Keep letters/digits only — punctuation and spacing carry no identity.
    return "".join(ch for ch in folded if ch.isalnum())


def match_candidates(existing: list[Task], candidates: list) -> tuple[dict, PlanDiff]:
    """Pair this import's candidates with the project's existing tasks.

    Returns `({candidate_index: Task}, PlanDiff)`. The mapping holds the tasks
    whose ids should be reused; the diff describes the whole operation for
    review and for the completion-record migration.

    First-come-first-served on duplicates: if a document lists the same
    normalised title twice, the second occurrence is a genuinely new node rather
    than a second claim on the same id.
    """
    by_norm: dict[str, list[Task]] = {}
    for t in existing:
        by_norm.setdefault(normalize_title(t.title), []).append(t)

    diff = PlanDiff()
    pairing: dict[int, Task] = {}
    claimed: set[str] = set()

    for i, c in enumerate(candidates):
        norm = normalize_title(c.title)
        pool = by_norm.get(norm, [])
        match = next((t for t in pool if t.id not in claimed), None)
        if match is None:
            diff.added.append(c.title)
            continue
        claimed.add(match.id)
        pairing[i] = match
        renamed = match.title != c.title
        diff.matched.append({
            "task_id": match.id,
            "old_title": match.title,
            "new_title": c.title,
            "renamed": renamed,
        })
        if renamed:
            diff.completion_migrations.append({
                "old_key": f"{match.id}::{match.title}",
                "new_key": f"{match.id}::{c.title}",
            })

    for t in existing:
        if t.id in claimed:
            continue
        # Done work stays even when the revised document drops it — the record of
        # having finished it is a fact about the past, not part of the new plan.
        if _is_done(t):
            diff.kept_done.append({"task_id": t.id, "title": t.title})
        else:
            diff.removed.append({"task_id": t.id, "title": t.title, "was_done": False})

    return pairing, diff


def _is_done(t: Task) -> bool:
    from storage import completion_store
    rec = completion_store.get(f"{t.id}::{t.title}")
    return rec is not None and rec.status == CompletionStatus.done


def migrate_completion_keys(migrations: list[dict]) -> int:
    """Move completion records onto the renamed nodes' keys.

    Without this a rename silently loses the done flag: block_key is half title,
    so the record would still be sitting under the old string with nothing
    looking it up. Returns how many records actually moved.
    """
    from storage import completion_store, save_completion_store

    moved = 0
    for m in migrations:
        rec = completion_store.pop(m["old_key"], None)
        if rec is None:
            continue
        rec.block_key = m["new_key"]
        rec.title = m["new_key"].split("::", 1)[1]
        completion_store[m["new_key"]] = rec
        moved += 1
    if moved:
        save_completion_store()
    return moved
