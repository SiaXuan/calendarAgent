"""
Decomposition eval runner (task_agent.rank_and_decompose).

Two modes:
  smoke — forces the deterministic fallback (LLM call is stubbed to raise), so it
          validates the harness, data flow, and Pydantic wiring without a network
          call. Fast, free, CI-safe. Won't catch LLM *quality* regressions.
  full  — real LLM. Measures actual decomposition quality (over-splitting etc.).
          Needs ANTHROPIC_API_KEY. Costs a few cents.

    .venv/bin/python -m tests.eval.run_decomp_eval --mode smoke
    .venv/bin/python -m tests.eval.run_decomp_eval --mode full

Structured results are written to tests/eval/decomp_last_run.json (diffable across
runs for review). Per-case status:
  smoke: smoke_ok | smoke_fail | error
  full : pass | fail | xfail (known-bad, still failing) | xpass (known-bad now
         passing → go remove the xfail flag) | error
Exit code is non-zero if any case is smoke_fail / fail / error (xfail/xpass are OK).
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


class _BoomLLM:
    """Stub that makes rank_and_decompose's LLM call raise → fallback path."""
    def with_structured_output(self, *a, **k):
        return self

    async def ainvoke(self, *a, **k):
        raise RuntimeError("smoke mode: forced LLM failure → heuristic fallback")


async def _run(mode: str) -> int:
    # load_dotenv BEFORE importing agents so the LLM client picks up the key in
    # full mode (the client is built at import time in agents/llm.py).
    load_dotenv()
    if mode == "full" and not os.getenv("ANTHROPIC_API_KEY"):
        print("✗ full 模式需要 ANTHROPIC_API_KEY(.env)。")
        return 1

    from agents import task_agent
    from tests.eval.decomp_scenarios import CASES, EVAL_DATE

    if mode == "smoke":
        task_agent.sonnet = _BoomLLM()   # force the deterministic fallback

    rows: list[dict] = []
    for case in CASES:
        t0 = time.time()
        try:
            subs = await task_agent.rank_and_decompose(case.tasks, EVAL_DATE)
            elapsed = round(time.time() - t0, 2)
            titles = [s.title for s in subs]
            if mode == "smoke":
                ok = len(subs) >= 1
                status = "smoke_ok" if ok else "smoke_fail"
                detail = f"fallback 出 {len(subs)} 步" if ok else "fallback 无输出"
            else:
                ok, detail = case.quality_check(subs)
                if case.xfail:
                    status = "xpass" if ok else "xfail"
                else:
                    status = "pass" if ok else "fail"
            rows.append({
                "name": case.name, "status": status, "n_subtasks": len(subs),
                "titles": titles, "detail": detail, "elapsed_s": elapsed,
                "xfail_flag": case.xfail,
            })
        except Exception as exc:
            rows.append({
                "name": case.name, "status": "error", "n_subtasks": 0,
                "titles": [], "detail": str(exc)[:100],
                "elapsed_s": round(time.time() - t0, 2), "xfail_flag": case.xfail,
            })

    # ── report ──────────────────────────────────────────────────────────────
    print(f"\n=== Decomposition Eval [{mode}] ===")
    bad = {"smoke_fail", "fail", "error"}
    n_bad = sum(1 for r in rows if r["status"] in bad)
    for r in rows:
        mark = "✗" if r["status"] in bad else ("!" if r["status"] == "xpass" else "✓")
        line = f"  {mark} {r['name']:38} [{r['status']}]"
        if r["detail"]:
            line += f"  — {r['detail']}"
        print(line)
        if r["status"] == "xpass":
            print("      ↑ 已知 bad case 现在通过了 —— 去 decomp_scenarios.py 把 xfail 去掉")
    print(f"\n{len(rows) - n_bad}/{len(rows)} ok(xfail/xpass 不计失败)")

    out = Path(__file__).parent / "decomp_last_run.json"
    out.write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "ok": len(rows) - n_bad, "total": len(rows),
        "results": rows,
    }, ensure_ascii=False, indent=2))
    print(f"结果写入 {out}")
    return 1 if n_bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Decomposition eval runner")
    ap.add_argument("--mode", choices=["smoke", "full"], default="smoke",
                    help="smoke=强制 fallback、离线快;full=真 LLM 测质量")
    args = ap.parse_args()
    return asyncio.run(_run(args.mode))


if __name__ == "__main__":
    raise SystemExit(main())
