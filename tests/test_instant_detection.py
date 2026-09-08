"""
Instant pre-classification (`api/tasks.py::_detect_instant`).

This had zero tests while it silently stopped producing instants for months —
the existing suite only ever hand-injected `is_instant=True` and exercised the
plumbing downstream. These pin the classifier itself.

Tri-state contract: True = confident errand, False = confident real work,
None = can't tell, defer to the LLM during decomposition.
"""
import pytest

from agents.task_agent import _accepts_instant, _is_instant_task
from models.task import CognitiveLoad, Priority, Task
from api.tasks import _detect_instant


@pytest.mark.parametrize("title", [
    "缴费", "完成缴费", "买菜", "取快递", "打电话给房东", "确认预约",
    "交房租", "充值话费", "Pay rent", "Submit form", "print paperwork",
])
def test_errands_are_instant(title):
    assert _detect_instant(title) is True


def test_completion_verb_does_not_veto():
    """
    The regression that killed Chinese instants: '完成' was an exclusion word,
    and a huge share of Chinese reminders are phrased "完成 X" — so "完成缴费"
    lost to the veto despite being exactly the errand this should catch.
    """
    assert _detect_instant("完成缴费") is True
    assert _detect_instant("完成注册") is True
    # ...while the actual deliverable nouns still win
    assert _detect_instant("完成作业") is False


@pytest.mark.parametrize("title", [
    "submit homework", "提交 CV Assessment", "作业5：负载测试与线程",
    "最终项目提交（海报、报告与展示）", "write essay", "期末复习",
])
def test_deliverables_are_not_instant(title):
    assert _detect_instant(title) is False


@pytest.mark.parametrize("title", [
    "contest signup",     # 'test' must not match inside "contest"
    "label the axes",     # 'lab'
    "read the newspaper", # 'paper'
    "download apps",      # 'ps'
    "finalize slides",    # 'final'
])
def test_exclusions_match_whole_words_only(title):
    """Bare-substring exclusions vetoed unrelated titles; these should fall
    through to the LLM rather than being ruled out."""
    assert _detect_instant(title) is None


def test_unknown_defers_to_llm():
    """Neither list matched — the answer is "don't know", not "no". Returning
    False here is what made every unclassified path assert "not an errand"."""
    assert _detect_instant("leetcode 二题") is None
    assert _detect_instant("投 10+ 简历") is None


def test_notes_are_considered():
    """A bare reminder title with the real content in its notes."""
    assert _detect_instant("待办", notes="记得交房租") is True


# ─── the deterministic gate on Claude's verdict ──────────────────────────────

class _Item:
    def __init__(self, parent_id="t1", is_instant=True):
        self.parent_id = parent_id
        self.is_instant = is_instant


def _task(**kw):
    return Task(id="t1", title="x", priority=Priority.medium,
                cognitive_load=CognitiveLoad.light,
                estimated_hours=kw.pop("estimated_hours", 0.1), **kw)


def test_gate_accepts_a_genuine_errand():
    assert _accepts_instant(_Item(), _task(), 1) is True


def test_gate_rejects_when_prefilter_already_said_no():
    """An explicit keyword False outranks the LLM — it did before the LLM was
    allowed to answer at all, and it still should."""
    assert _accepts_instant(_Item(), _task(is_instant=False), 1) is False


def test_gate_rejects_long_estimates():
    assert _accepts_instant(_Item(), _task(estimated_hours=2.0), 1) is False


def test_gate_rejects_multi_step_tasks():
    """Anything split into steps isn't a thing you just do."""
    assert _accepts_instant(_Item(), _task(), 3) is False


def test_gate_rejects_when_llm_said_no():
    assert _accepts_instant(_Item(is_instant=False), _task(), 1) is False


def test_undecided_task_still_goes_to_claude():
    """`None` must not short-circuit the LLM — that's the whole point of the
    tri-state. Only a confident True skips decomposition."""
    assert _is_instant_task(_task(is_instant=None, estimated_hours=0.5)) is False
    assert _is_instant_task(_task(is_instant=True, estimated_hours=0.5)) is True
