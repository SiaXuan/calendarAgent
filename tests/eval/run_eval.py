"""
Agent eval runner (Phase: 核心闭环 S5).

Runs the regression scenarios in scenarios.py against the REAL agent (real LLM)
and prints a PASS/FAIL table. Use it to catch behavior regressions after you
change the system prompt or tools.

    .venv/bin/python -m tests.eval.run_eval

Costs a handful of LLM calls (a few cents). Needs ANTHROPIC_API_KEY in .env.
Results are also written to tests/eval/last_run.json for diffing across runs.

Note: the chat agent only reads schedule_store (no CalDAV / Reminders), so we
just seed a known schedule per scenario — no network mocking needed.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


async def _run():
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("✗ 没有 ANTHROPIC_API_KEY，无法跑 live eval。")
        return 1

    import storage
    from graphs.agent_run import run_chat_agent
    from models.schedule import DaySchedule
    from tests.eval.scenarios import EVAL_DATE, SCENARIOS, _result_blocks

    # CRITICAL: redirect schedule persistence to a throwaway file so the eval
    # (which commits schedules → save_schedule_store) never clobbers the user's
    # real data/schedule_store.json.
    storage._SCHEDULE_FILE = Path(__file__).parent / "_eval_schedule_tmp.json"

    rows = []
    for sc in SCENARIOS:
        # Fresh world per scenario.
        storage.schedule_store.clear()
        storage.schedule_version.clear()
        storage.pending_proposals.clear()
        storage.chat_sessions.clear()
        storage.schedule_store[EVAL_DATE] = DaySchedule(
            date=EVAL_DATE, energy_curve=sc.energy_curve, blocks=sc.blocks,
            unscheduled=[], health_summary="",
        )
        storage.schedule_version[EVAL_DATE] = 1

        try:
            res = await run_chat_agent(EVAL_DATE, sc.message)
        except Exception as exc:
            rows.append((sc.name, False, "EXCEPTION", str(exc)[:60]))
            continue

        term_ok = res.terminal_state in sc.expect_terminals
        detail = ""
        check_ok = True
        if sc.check is not None:
            check_ok, detail = sc.check(_result_blocks(res), res)
        passed = term_ok and check_ok
        if not term_ok:
            detail = f"终止态={res.terminal_state}（期望{sc.expect_terminals}）; " + detail
        rows.append((sc.name, passed, res.terminal_state, detail))

    # ── report ────────────────────────────────────────────────────────────
    print("\n=== Agent Eval ===")
    n_pass = sum(1 for r in rows if r[1])
    for name, passed, term, detail in rows:
        mark = "✓" if passed else "✗"
        line = f"  {mark} {name:24} [{term}]"
        if detail:
            line += f"  — {detail}"
        print(line)
    print(f"\n{n_pass}/{len(rows)} passed")

    out = Path(__file__).parent / "last_run.json"
    out.write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "passed": n_pass, "total": len(rows),
        "results": [
            {"name": n, "passed": p, "terminal": t, "detail": d}
            for n, p, t, d in rows
        ],
    }, ensure_ascii=False, indent=2))
    print(f"结果写入 {out}")
    return 0 if n_pass == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
