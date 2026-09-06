"""
Known bad case — a one-off errand with a deadline gets over-decomposed.

观察到的问题(真机,2026-09):"仓库 move out"(搬出物品 + 归还钥匙 + 退租)、
"地址迁移" 这种**有截止日的一次性杂事**,目前被 task_agent 当成"需要每天推进的
多步深度任务":
  (a) 拆成多个认知子步骤(「清点物品/确认计划」+「执行搬出/归还钥匙」),
  (b) 从今天到 deadline 每天重复排全套(8/31 和 9/1 排了一模一样的步骤)。
期望行为:这类 errand 不该拆认知步骤(一个块搞定),且临近 deadline 排一次即可。

根因是 ARCHITECTURE §4/§8 记录的 **deadline-vs-appointment 缺口**:`is_instant`
纯靠关键词,"搬出"不含 instant 触发词 → 被判成常规多步任务。功能尚未实现,所以此
测试当前 **xfail**;实现后会 xpass,提醒回来去掉 xfail 标记。

真 LLM 复现(2026-09 手动跑,~4s):"仓库 move out" 被拆成
  1. "Pack and move out all items from the warehouse" (30min)
  2. "Return keys and complete lease termination paperwork" (25min)

**注意运行机制**:pytest 常规套件不保证真 LLM(conftest 会先 import agents.llm,
此时 key 可能还没 load → 客户端降级、rank_and_decompose 走启发式 fallback)。所以
本测试在**走 fallback 时 skip**(不假装测了 LLM),只有真 LLM 生效才作为 xfail 记录。
要真跑,像 run_eval.py 那样先 load_dotenv 再 import,或:
    .venv/bin/python -m pytest tests/eval/test_decomposition_badcase.py -v
"""
import os
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

from agents.task_agent import rank_and_decompose
from models.task import CognitiveLoad, Priority, Task

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="需要 ANTHROPIC_API_KEY 跑真 LLM eval",
)


@pytest.mark.xfail(
    reason="deadline-vs-appointment 缺口(ARCHITECTURE §4/§8):有截止日的一次性"
           "杂事被拆成多认知步骤。实现后此测试转 xpass,提醒去掉标记。",
    strict=False,
)
async def test_oneoff_errand_not_over_decomposed():
    """一次性杂事(搬家/退租)应当作单个块,不拆成多个认知子步骤。"""
    today = date(2026, 6, 23)
    errand = Task(
        id="warehouse_moveout",
        title="仓库 move out",
        description="搬出仓库物品、归还钥匙、完成退租手续",
        priority=Priority.high,
        cognitive_load=CognitiveLoad.light,
        estimated_hours=1.0,
        deadline=today + timedelta(days=1),   # 明天截止
        is_instant=False,
    )
    subs = await rank_and_decompose([errand], today)
    mine = [s for s in subs if s.parent_id == "warehouse_moveout"]
    # 启发式 fallback 用通用 "(part N)" 标题 → 说明真 LLM 没生效,此 case 无意义。
    if any("(part" in s.title for s in mine):
        pytest.skip("rank_and_decompose 走了启发式 fallback,非真 LLM(见 docstring)")
    # 一次性杂事不该被拆成多个认知步骤 —— 一个块搞定即可。
    assert len(mine) == 1, f"被拆成 {len(mine)} 步:{[s.title for s in mine]}"
