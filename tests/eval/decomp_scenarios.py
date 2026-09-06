"""
Decomposition eval scenarios — exercises `task_agent.rank_and_decompose`.

Separate from `scenarios.py` (which evals the chat agent). Each case seeds one
task and declares a **quality check** used by the full (real-LLM) mode. `xfail`
flags a known-bad case (a documented gap): full mode records it as an expected
failure until the underlying feature lands, and flips to `xpass` once it's fixed
— that flip is the signal to come remove the flag.

Two run modes live in `run_decomp_eval.py`:
  - smoke: forces the deterministic fallback (no LLM) — checks the harness, data
    flow, and Pydantic wiring run clean. Fast, free, CI-safe.
  - full:  real LLM — measures actual decomposition quality.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from models.task import CognitiveLoad, Priority, Task

EVAL_DATE = date(2026, 6, 23)


@dataclass
class DecompCase:
    name: str
    tasks: list[Task]
    # quality_check(subtasks) -> (passed, detail). Runs in full mode only.
    quality_check: Callable
    xfail: bool = False            # known-unfixed bad case
    note: str = ""


def _single_block(subs):
    """A one-off errand should be a single block, not multiple cognitive steps."""
    n = len(subs)
    return n == 1, f"拆成 {n} 步:{[s.title for s in subs]}"


CASES: list[DecompCase] = [
    DecompCase(
        name="oneoff_errand_not_over_decomposed",
        tasks=[Task(
            id="warehouse_moveout",
            title="仓库 move out",
            description="搬出仓库物品、归还钥匙、完成退租手续",
            priority=Priority.high,
            cognitive_load=CognitiveLoad.light,
            estimated_hours=1.0,
            deadline=EVAL_DATE + timedelta(days=1),
            is_instant=False,
        )],
        quality_check=_single_block,
        xfail=True,
        note="deadline-vs-appointment 缺口(ARCHITECTURE §4/§8):有截止日的一次性"
             "杂事被拆成多认知步骤(真 LLM 实测拆 2 步)+ 每天重排。期望:单块、临近排一次。",
    ),
]
