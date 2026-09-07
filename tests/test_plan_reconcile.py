"""
Title normalisation + candidate matching for re-import reconcile.

The endpoint-level behaviour lives in tests/test_plan_import.py; this pins the
matcher itself, because where the normaliser draws the line — cosmetic edit vs
genuine rewording — is the whole design decision. Matching too eagerly
transplants completion history onto different work; matching too timidly loses
it on a renumbered heading.
"""
from agents.plan_reconcile import match_candidates, normalize_title
from models.plan_import import CandidateTask
from models.task import CognitiveLoad, Priority, Task


def _task(title, tid="t1"):
    return Task(id=tid, title=title, estimated_hours=1.0, project_id="p1",
                priority=Priority.medium, cognitive_load=CognitiveLoad.medium)


# ─── normalisation ───────────────────────────────────────────────────────────

def test_normalize_ignores_cosmetic_edits():
    same = [
        "Read chapter one",
        "read chapter one",
        "Read Chapter One!",
        "  Read chapter one  ",
        "3. Read chapter one",
        "3) Read chapter one",
        "- Read chapter one",
        "Week 3 — Read chapter one",
    ]
    assert len({normalize_title(t) for t in same}) == 1


def test_normalize_folds_fullwidth_punctuation():
    """A doc round-tripped through another editor comes back full-width."""
    assert normalize_title("第3周：读第一章") == normalize_title("第 3 周: 读第一章")


def test_normalize_keeps_genuinely_different_titles_apart():
    assert normalize_title("Read chapter one") != normalize_title("Read chapter two")
    # Spelled-out vs numeric is a real rewording, not formatting — mapping them
    # together would let unrelated nodes inherit each other's completion history.
    assert normalize_title("Read chapter one") != normalize_title("Read chapter 1")


# ─── matching ────────────────────────────────────────────────────────────────

def test_match_pairs_by_normalised_title_and_flags_rename():
    existing = [_task("3. Read chapter one")]
    pairing, diff = match_candidates(existing, [CandidateTask(title="Read Chapter One")])

    assert pairing[0].id == "t1"
    assert diff.matched == [{
        "task_id": "t1", "old_title": "3. Read chapter one",
        "new_title": "Read Chapter One", "renamed": True,
    }]
    assert diff.completion_migrations == [
        {"old_key": "t1::3. Read chapter one", "new_key": "t1::Read Chapter One"}
    ]


def test_unchanged_title_needs_no_migration():
    pairing, diff = match_candidates([_task("Read chapter one")],
                                     [CandidateTask(title="Read chapter one")])
    assert pairing[0].id == "t1"
    assert diff.completion_migrations == []
    assert diff.matched[0]["renamed"] is False


def test_added_and_removed_are_reported():
    existing = [_task("Keep me", "t1"), _task("Drop me", "t2")]
    _, diff = match_candidates(existing, [
        CandidateTask(title="Keep me"), CandidateTask(title="Brand new"),
    ])
    assert diff.added == ["Brand new"]
    assert [r["title"] for r in diff.removed] == ["Drop me"]


def test_duplicate_titles_claim_distinct_tasks_then_count_as_new():
    """
    A doc listing the same heading twice gets one id reused and one fresh node —
    the second occurrence has no prior identity to inherit.
    """
    existing = [_task("Lab", "t1")]
    pairing, diff = match_candidates(existing, [
        CandidateTask(title="Lab"), CandidateTask(title="Lab"),
    ])
    assert pairing == {0: existing[0]}
    assert diff.added == ["Lab"]


def test_first_import_has_nothing_to_reconcile():
    pairing, diff = match_candidates([], [CandidateTask(title="Read chapter one")])
    assert pairing == {}
    assert diff.is_first_import
    assert diff.added == ["Read chapter one"]
