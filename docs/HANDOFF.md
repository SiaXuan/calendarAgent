# 交接（当前一次）

> **这份只写「当前这次」交接，每次交接会整份覆盖它** —— 不累积历史、不写全局。
> 全局蓝图与进度看 [ROADMAP.md](ROADMAP.md)；现状/决策/技术债看 [ARCHITECTURE.md](ARCHITECTURE.md)。
> 历史决策不往这里堆，落地即写进 ARCHITECTURE 对应章节。

**日期**：2026-07-29
**测试**：`.venv/bin/python -m pytest -q --ignore=tests/eval` → **288 passed**（`swift build` + `./make_app.sh` 均通过）。
**git**：本次一组 commit 在 `main`（后端排序 / 前端 UX / 文档重构），**未 push**。

## 这次做完的：Swift 前端 UX 修复 + 完成态接线 + 同任务阶段保序

围绕真机试用暴露的一串问题（Swift 侧栏为主 + 少量后端）：

- **悬浮侧栏刷新**（关键）：`.floating` 非-key 窗口里异步 `@State` 更新不上屏，要重新 hover 才刷。修法：展开时挂 30Hz `Timer` 泵 run loop（`SidebarWindowController.setDisplayPump`）。详见 ARCHITECTURE §10.5。
- **同一任务阶段保序**（后端）：`scheduler_agent.generate_schedule` 把同父任务子任务成组、按拆解顺序处理 + 给后一阶段 `min_start` 下限，修掉「先练习后做计划」反序；聊天调整 agent prompt 加「保持先后」（`graphs/agent_run.py`）。新增 2 个回归测试。**独立任务间 A→B 依赖仍没做**（需 `depends_on`，用户暂缓）。
- **完成态接 UI**：行内「完成勾」→ `POST /schedule/{date}/blocks/{block_key}/complete` → `completion_store`（喂复盘）。`DayflowScheduleBlock.isDone` 解码 + 乐观切换。sync 与 done 拆成两个图标（日历图标=写日历，✓=完成）；两个「Sync All」也带上同一日历图标。
- **Apply 提案不再静默失败**：非 success 时清掉失效卡 + 状态栏显因 + 刷新最新日程（`confirmAgentProposal`）。根因（提案纯内存、confirm 不带 proposal_id）未除。
- **杂项 UI**：状态栏请求中显示 spinner；±按钮/完成圈对比度（字色跟反相的 `upcomingPrimaryColor`，见 §10.5）；任务标签 hover 说明；时长文本不再被标签挤截断；`make_app.sh` 的 `$APP…` unbound 变量修掉。

## 待真机验证（都单测过，GUI 端到端没跑）

- 点完成勾：标题划掉、勾变绿、`data/completion_store.json` 出记录；重新生成/同步后仍保持。
- 侧栏展开静止不动时，流式能量曲线/任务卡、睡眠输入后的曲线重算能**实时**刷（不用重新 hover）。
- 同任务两阶段：不会再出现后置阶段排在前置之前。
- Apply 提案：不改后端、5 分钟内点 → 套用成功刷新；若失效则卡片消失且状态栏给原因。

## 建议下一步

线 B 最顺：**复盘 / heatmap 视图**（ROADMAP 线 B Step 3 后半）——完成勾数据已在流，后端 `GET /completions/heatmap` + 前端 `fetchHeatmap` 都现成，只差视图。
或推进线 A 的 IP：Phase E（论文规则引擎，叙事最强）/ Phase B（接 Apple Health）。全局取舍见 [ROADMAP.md](ROADMAP.md)。

## 怎么跑

```bash
# 后端（改 .py 后 uvicorn --reload 自动重载；跑前端时别狂改后端会掐断流）
.venv/bin/uvicorn main:app --reload

# 前端：改 Swift 后必须重新打包再开；别用 swift run（EventKit 权限过不了）
cd cal_swift_frontend && ./make_app.sh && open ScheduleAgent.app

# 测试（eval 会打真 LLM，日常跑排除它）
.venv/bin/python -m pytest -q --ignore=tests/eval
```

## 关键文件

| 干什么 | 文件 |
|---|---|
| 每日生成 DAG / 节点（窗口闸门 + 注入项目时段）| `graphs/schedule_graph.py`、`agents/nodes.py` |
| 聊天 agent（ReAct + scratch + gate）| `graphs/agent_run.py`、`agents/tools/schedule_tools.py`、`agents/scratch.py` |
| 排程器（同任务阶段保序）| `agents/scheduler_agent.py` |
| 多天规划 + 结转 + 项目服务 | `agents/multiday_planner.py`、`agents/project_service.py` |
| 项目 API（chat/plan/multiday/import/replan/complete/heatmap）| `api/projects.py`、`api/chat.py` |
| 存储 | `storage.py`、`main.py`(lifespan) |
| 前端主侧栏（流/健康/完成勾/sync/重排）| `cal_swift_frontend/.../SidebarView.swift` |
| 前端悬浮窗 + 刷新泵 | `cal_swift_frontend/.../ScheduleAgentApp.swift` |
| 前端 EventKit 执行层 | `cal_swift_frontend/.../AppleCalendarAdapter.swift` |
| 前端后端客户端 + DTO（含 `is_done`/`setBlockCompletion`/`fetchHeatmap`）| `cal_swift_frontend/.../DayflowAPIClient.swift`、`.../DayflowScheduleModels.swift` |
| 打包 | `cal_swift_frontend/make_app.sh` |
