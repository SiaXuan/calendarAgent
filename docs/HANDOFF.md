# 交接（当前一次）

> **这份只写「当前这次」交接，每次交接整份覆盖** —— 不累积历史、不写全局。
> 全局蓝图与进度看 [ROADMAP.md](ROADMAP.md)；现状/决策/技术债看 [ARCHITECTURE.md](ARCHITECTURE.md)。
> 落地即写进 ARCHITECTURE 对应章节，这里只留「到哪了 / 下一步」。

**日期**：2026-08-22
**测试**：`.venv/bin/python -m pytest -q --ignore=tests/eval` → **299 passed**（`swift build` + `./make_app.sh` 均通过）。
**git**：一串 commit 在 `main`，**未 push**。

## 最近这批做完的（细节见 ARCHITECTURE §10.5–§10.7）

真机试用暴露的一连串问题，已修：

- **`block_key` 稳定化**（关键，pin/complete/结转「重排即失效、重启才好」的总根）：每日拆解现在缓存（`subtask_cache`，内容没变就复用 → 标题稳 → key 稳），不改 key 格式、零迁移。§10.7。
- **跨天自动前移**：监听 `NSCalendarDayChanged` + `didWake`，挂机过夜自动切到今天并跑结转，不用重启。§10.7。
- **删除卡片**：'−' 减到只剩 1 个番茄再按 → 确认框 → `POST .../blocks/{key}/remove` 从今日移除。§10.7。
- **认知负荷分类器**：改用 `with_structured_output`（优先 json_schema、降级 tool use），根除 JSON 围栏崩溃。§10.7。
- **空/默认名提醒过滤**：默认名「新提醒事项」且无备注 → 跳过；有备注 → 用备注首行当标题。§10.7。
- **完成态接 UI**（行内完成勾 → completion_store，喂复盘）、**同任务阶段保序**、**Apply 提案不再静默失败**、**悬浮窗刷新泵 / 对比度 / spinner**、**sync 与 done 拆图标**。§10.5–§10.7。

## 待真机验证（都单测过，GUI 端到端没逐条跑）

- 输睡眠/重新生成后**不重启**拖任意卡片 → 不再 404；勾完成后重排仍认得。
- 挂机过夜（或调系统时间过午夜/睡眠跨午夜）→ 侧栏自动切今天、昨天没做完的「继续」上来。
- '−' 到 `1 x 25m` 再按 → 弹框 → Delete → 卡片消失、`data/schedule_store.json` 该块没了。
- 空提醒（默认名无备注）不再排出「处理新提醒事项内容」；有备注的按备注排。
- 埋点：发 chat 调整 → `data/agent_run_log.jsonl` 出 run；Apply→`applied`、Keep as is→`rejected`。

## 下一步（当前正在做）

**复盘 / heatmap 视图**（ROADMAP 线 B Step 3 后半）—— 完成勾数据已在流，后端 `GET /completions/heatmap` + 前端 `fetchHeatmap` 都现成，只差一个视图（commit 热力图墙 + 可选按项目/周期的完成情况）。**铁律：进度数字只来自 `completion_store`，LLM 只写叙述、不自报数字。**

之后（未做）：eval 导出脚本 `scripts/export_eval_cases.py`（攒够真实 case 再做）；线 A 的 IP（Phase E 论文规则引擎 / Phase B 接 Apple Health，路径见 ROADMAP）。

## 怎么跑

```bash
# 后端（改 .py 后 uvicorn --reload 自动重载；跑前端时别狂改后端会掐断流）
.venv/bin/uvicorn main:app --reload
# 前端：改 Swift 后必须重新打包再开；别用 swift run（EventKit 权限过不了）
cd cal_swift_frontend && ./make_app.sh && open ScheduleAgent.app
# 测试（eval 会打真 LLM，日常排除它）
.venv/bin/python -m pytest -q --ignore=tests/eval
```

## 关键文件

| 干什么 | 文件 |
|---|---|
| 每日生成 DAG / 节点（窗口闸门 + 拆解缓存 + 注入项目时段）| `graphs/schedule_graph.py`、`agents/nodes.py` |
| 聊天 agent（ReAct + scratch + gate + 埋点）| `graphs/agent_run.py`、`agents/tools/schedule_tools.py`、`agents/scratch.py` |
| 排程器（同任务阶段保序）| `agents/scheduler_agent.py` |
| 多天规划 + 结转 + 项目服务 + 完成/heatmap | `agents/project_service.py`、`agents/multiday_planner.py` |
| 任务/提醒同步 + 分类器 + 空提醒过滤 | `api/tasks.py` |
| 日程 API（pin/complete/remove/heatmap/changeset/stream）| `api/schedule.py`、`api/projects.py`、`api/chat.py` |
| 存储（含 subtask_cache / agent_run_log）| `storage.py`、`main.py`(lifespan) |
| 前端主侧栏 | `cal_swift_frontend/.../SidebarView.swift` |
| 前端悬浮窗 + 刷新泵 | `cal_swift_frontend/.../ScheduleAgentApp.swift` |
| 前端 EventKit 执行层 | `cal_swift_frontend/.../AppleCalendarAdapter.swift` |
| 前端后端客户端 + DTO（`fetchHeatmap`/`setBlockCompletion`/`removeScheduledBlock`…）| `cal_swift_frontend/.../DayflowAPIClient.swift`、`.../DayflowScheduleModels.swift` |
| 打包 | `cal_swift_frontend/make_app.sh` |
