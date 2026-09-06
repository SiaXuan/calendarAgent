"""
Offline smoke guard for the decomposition eval (see run_decomp_eval.py).

Runs in the normal pytest suite. It forces the deterministic fallback (no LLM)
and checks every decomposition scenario still runs clean end-to-end:
rank_and_decompose doesn't crash and produces valid Subtasks. It does NOT measure
LLM decomposition *quality* — that's the real-LLM "full" mode, run manually:

    .venv/bin/python -m tests.eval.run_decomp_eval --mode full

The known bad case (errand over-decomposition, ARCHITECTURE §4/§8) is recorded
there as an xfail that flips to xpass once fixed. Keeping quality out of the
offline suite is deliberate: pytest imports agents.llm before the key loads, so
the real LLM isn't reliably reachable here anyway.
"""
import pytest

from agents import task_agent
from tests.eval.decomp_scenarios import CASES, EVAL_DATE


class _BoomLLM:
    def with_structured_output(self, *a, **k):
        return self

    async def ainvoke(self, *a, **k):
        raise RuntimeError("smoke: forced LLM failure → heuristic fallback")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_decomp_fallback_smoke(case, monkeypatch):
    """Forcing the LLM to fail must leave rank_and_decompose producing valid
    Subtasks via the heuristic fallback — the harness + data flow stay intact."""
    monkeypatch.setattr(task_agent, "sonnet", _BoomLLM())
    subs = await task_agent.rank_and_decompose(case.tasks, EVAL_DATE)
    assert subs, f"{case.name}: fallback 无输出"
    assert all(s.parent_id for s in subs), "fallback subtask 缺 parent_id"
    assert all(s.estimated_minutes >= 1 for s in subs), "fallback subtask 时长非法"
