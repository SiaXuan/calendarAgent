# Phase 4 交接（最新：2026-07-29）

> 接着干的清单。配合 [ARCHITECTURE.md](ARCHITECTURE.md)（§0 迁移状态最全、§10 踩坑）一起看。
> 结论/踩坑记在 ARCHITECTURE；这份是「现在到哪了、下一步做什么」。
> 测试：`.venv/bin/python -m pytest -q --ignore=tests/eval` → **288 passed**。

## 总路线图（两条计划线 —— 别只盯 EventKit）

权威 plan 都在 `~/.claude/plans/`（`docs/phase3-plan.md` 已删，别再找）：

**A. 大框架 `md-phase3-polished-rocket.md`** —— 把项目从单一排程工具升级成「可扩展的个性化健康规划框架」，三层模型（Profile / Core Graph / Data+Rules）+ 五个 Phase。**核对代码后的真实状态：**
- **Phase A · LangGraph 迁移 + task_kind** ✅ 完成（`graphs/*`、`agents/llm.py`、agent/workflow 分层、scratch + `classify_impact` + 乐观并发都在）。
- **Phase B · 多源健康数据**（Apple Health / Oura / WHOOP / Garmin，抽象 `HealthAdapter` + `HEALTH_SOURCE`）🔭 **未开始**（设计上刻意降级；现在只有手动睡眠输入，无 `integrations/health/`）。
- **Phase C · LangMem 长期记忆 + 学习闭环** 🔨 **部分**：C.1 storage / C.2 Inspector / C.3 规则触发写入+读取 已做（`memory/store.py|observations.py|retrieval.py`，仅 `schedule_prefs` namespace 通电）；**C.4（每周 LLM 反思 + 衰减 cron）未做**（无 `memory_extractor`）。见 memory `phase_c_status`。
- **Phase D · 用户自带 MCP**（`custom_mcp_servers` + `graphs/user_mcp.py` + chronotype 下拉）🔭 **未开始**（chronotype 目前只是 `health_agent` 按就寝时间算的中间值，不是可配置字段）。
- **Phase E · 研究论文驱动的健康规则引擎**（`agents/health_rules.py`，每条规则带论文引用 + health card 可展开看依据）🔭 **未开始**（现在能量曲线是 `health_agent` 里的高斯规则，不是可解释的引用式规则集）。

**B. EventKit + 项目层 `whimsical-seeking-shannon.md`（Step 0–4）+ `parsed-gliding-platypus.md`** —— CLAUDE.md 说的「Phase 4」落地那半。剩余见下方「Phase 4 剩余」。

> ⚠️ CLAUDE.md 的「Phase 4 (in progress)」把 LangGraph/LangMem/多源健康/研究规则/MCP 全算进去，实际只有 A 完成、C 部分，**B/D/E 没动**；它引用的 `docs/phase3-plan.md` 已删（应指向上面两份 plan）——建议顺手修。

---

## 本会话（2026-07-29）做完的：Swift 前端 UX 修复 + 完成态接线 + 同任务阶段保序

围绕真机试用暴露的一串问题（都在 Swift 侧栏 + 少量后端）：

- **悬浮侧栏刷新**（关键）：`.floating` 非-key 窗口里异步 `@State` 更新不上屏，要重新 hover 才刷。修法：展开时挂 30Hz `Timer` 泵 run loop（`SidebarWindowController.setDisplayPump`）。详见 ARCHITECTURE §10.5。
- **同一任务的阶段保序**（后端）：`scheduler_agent.generate_schedule` 把同父任务子任务成组、按拆解顺序处理 + 给后一阶段 `min_start` 下限，修掉「先练习后做计划」反序；聊天调整 agent prompt 加「保持先后」（`graphs/agent_run.py`）。新增 2 个回归测试。**独立任务间 A→B 依赖仍没做**（需 `depends_on` 字段，用户暂缓）。
- **完成态接 UI**：行内「完成勾」→ `POST .../complete` → `completion_store`（喂复盘）。`is_done` 前端解码 + 乐观切换。sync 与 done 拆成两个图标；两个「Sync All」也带上同一日历图标。
- **Apply 提案不再静默失败**：非 success 时清掉失效卡 + 状态栏显示原因 + 刷新最新日程（`confirmAgentProposal`）。根因（提案纯内存、confirm 不带 proposal_id）未除。
- **杂项 UI**：状态栏请求中显示 spinner；±按钮/完成圈对比度（字色跟反相的 `upcomingPrimaryColor`，见 §10.5）；任务标签 hover 说明（虽然原生 tooltip 在这窗口弹不出，见 §10.5）；时长文本不再被标签挤截断；`make_app.sh` 的 `$APP…` unbound 变量修掉。

**都过 `swift build` + `./make_app.sh`；后端 288 passed。GUI 端到端仍需真机点一遍（见下「待真机验证」）。**

### 待真机验证（本会话）
- 点完成勾：标题划掉、勾变绿、`data/completion_store.json` 出记录；重新生成/同步后仍保持。
- 展开侧栏静止不动时，流式能量曲线/任务卡、睡眠输入后的曲线重算能**实时**刷出来（不用重新 hover）。
- 同任务两阶段：不会再出现后置阶段排在前置之前。
- Apply 提案：不改后端、5 分钟内点 → 套用成功刷新；若失效则卡片消失且状态栏给原因。

## 本会话（2026-07-23）做完的

### 项目 = 一条有记忆的对话（chatbot，核心重构）
- 项目详情页改成**两栏**：左多轮对话、右计划节点（标题+日期）+ 多天排程 + 写入提醒。
- **一个 project 一条对话线、全程记忆**：`storage.project_chat_store`（按 project_id 存全历史，落盘）。
- **导入并进对话、没有硬意图闸门**：`POST /projects/{id}/chat`（multipart：message + 可选 file）。图→视觉、文档→parse 成文本，一起喂给会话 LLM（`agents/project_chat.converse`）。LLM 自己判断：是计划就把**完整新任务列表**返回 → 应用到右边；不明确就**反问兜底**（「你发的像 X，想…吗？」），绝不硬拒。
- 改计划时**同名任务保 id**（`project_service._apply_task_revision`）→ 进度/提醒不丢，重建快照。
- 旧 `POST /import`（+ 置信度闸门，阈值已放宽到 0.4、去掉 is_plan 一票否决）还在，但前端 chatbot 不再走它；前端 `submitComposer` 一律走 `/chat`。

### 多天规划器（Step 1.6）
- `agents/multiday_planner.py`：推理 LLM（`sonnet`，可 `LLM_REASON_MODEL` 换 opus）+ 贪心兜底 + 容量/deadline 校验。
- **只规划 deadline 在窗口内的项目节点**（`_in_window_project_nodes`），摊到 [今天, deadline] 里。**LLM 读 source_excerpt 想清楚阶段步骤**（读材料→实现→检查→提交），每步有意义标题 + 时长（30–120min，一步不打断），能收口就一天收口——不是切等长同名 block。
- 存 `multiday_plan_store`；每日调度 `rank_tasks_node` 注入当天 `chunk_subtasks_for_date`。
- 端点 `POST /projects/plan-multiday`、`GET /projects/{id}/multiday`；前端「排入多天日程」按钮 + 按天展示；容量由 `AppleCalendarAdapter.fixedMinutesByDate` 读未来 30 天本地日历算。

### 窗口闸门（语义校正）
- 窗口 = **只有 deadline ≤ 今天+`SCHEDULE_HORIZON_DAYS`（默认 5 天）或过期/无日期的**任务才进当天排程。远 deadline（几个月后）现在完全不排。`agents/nodes.py::_within_horizon`（每日提醒池）+ `_in_window_project_nodes`（项目节点）。

### 项目任务与调度池分离
- import/chat 建的项目任务进 **`project_task_store`**（独立落盘），**不进全局 `task_store`**（=每日调度池，`rank_tasks_node` 全量读它）。启动 `_migrate_project_tasks_out_of_task_store()` 把历史遗留的迁出（幂等，已迁）。`GET /plan` 没快照有任务时现建（老项目也显示）。

### reminder 按项目分组 + 颜色
- 每项目一个提醒列表（EKCalendar，名=项目名、色=`Project.color`）。`applyReminderChangeset(listName:colorHex:)`；项目页 ColorPicker + 「写入」按钮（走 `replan_project` 粗节点重新同步）。

### 其它
- **图片粘贴 → 视觉**：导入框 ⌘V 贴截图（自定义 `PastingTextEditor`/`ImagePastingTextView` 拦 `performKeyEquivalent`+`paste`，SwiftUI TextEditor 会吞 ⌘V）或 📷 按钮。后端 `plan_import_agent.extract_plan_from_image`。PDF 上限放开 25MB/60 页/80k 字。
- **每日日程加载动画**：Upcoming 区生成中显示转圈+骨架，不再像「今天没事」。
- **重新生成今天** 🔄：Upcoming 头 Sync All 左边的图标，强制重排（不吃缓存）。
- **日历权限解耦**：启动**不再主动弹**日历权限（ad-hoc 签名下每次重打包会重置、老弹）；生成只在**已授权**时读本地日历（`hasEventAccess`），否则降级。权限靠导入/写入那条 full-access 流程拿。
- **本地日历过滤**：`localCalendarEvents` / `fixedMinutesByDate` 排除 `.subscription`（节假日/节气/世界杯赛程）和 `.birthday` 及全天事件——之前「大暑」占满全天就是这个。
- **UI 文案精简**：存了 memory `terse-ui-copy`（别把解释性长段写进 UI）。

## 本会话（2026-07-24）做完的：每日动态重排（自动多天规划）+ 结转记忆

**触发点搬进每日生成（改掉了手动按钮）**：多天规划不再是项目页按钮，改为每日生成前**自动增量**跑
`project_service.ensure_multiday_plan(anchor_date, fixed_minutes_by_date)`——剪枝（删/全完成的 chunk）→
找「窗口内但还没排过」的新节点跑 LLM（**只排新节点**）→ 容量扣掉已排 chunk 占用。`POST /schedule/generate|stream`
跑图前先调它（`GenerateRequest` 加 `fixed_minutes_by_date`；前端 generate/stream 带 `fixedMinutesByDate(from:days:30)`）。
旧 `POST /projects/plan-multiday` 保留改 `force=True`（dev 后门，前端按钮 + `VM.planMultiday` 已删）。

**结转叠加层**：`multiday_plan_store`=底稿（不改），`completion_store`=完成叠加。`chunk_subtasks_for_date` 改为注入
「`date ≤ today` 且没勾完成」的 chunk——过去未完成的自动上浮，标 `carried_over`（前端 `displayTitle` 加「继续：」）。
**结转块保持原 title** → block_key 稳定 → 勾一次即收口不无限结转。

**对话可补充**：项目对话说「X 做完了/还没做完」→ `PlanChatResult.progress` → `_apply_chat_progress` 写/清完成记录。

测试 286 passed（新增 `tests/test_carryover.py` 10 个）。Swift `swift build` 通过。**后端逻辑全单测过，GUI 端到端没跑。**

### 待真机验证（本会话新增）
- 项目节点 deadline 滚进 5 天窗口 → 「重新生成今天」自动排入、**无需去项目点按钮**；项目页「多天排程」区自动显示分布。
- 今天不勾完成的项目时段 → 次日「重新生成」自动出现「继续：X」。
- 项目对话说「作业1 交了」→ 次日不再出现；说「还没做完」→ 继续结转。
- 确认 generate/stream 真的带上了 `fixed_minutes_by_date`（未授权日历时降级为 null，后端按满工时算）。

## 待真机验证（都单测过，GUI 端到端没跑）
- 项目对话整条链：贴大纲/截图 → 右边出节点 → 说「拆细/挪一挪」→ 节点变 → 「排入多天日程」→ 「写入」提醒。
- 首次「写入」要授权提醒；首次「排入多天/生成」要授权日历。
- 确认 uvicorn 日志不再联网读 CalDAV（世界杯赌博日历）。

## Phase 4 剩余（对照真 plan：`~/.claude/plans/whimsical-seeking-shannon.md` Step 0–4 + `parsed-gliding-platypus.md`）

> 权威 plan 在 `~/.claude/plans/`（不是被删的 `docs/phase3-plan.md`）；进度追踪在 memory `phase4-local-eventkit-migration`。
> **已完成**：Step 0 写入层加固、Step 1 项目层+完成追踪+完成感知 reconcile、Step 1.6 自动多天规划+结转、Step 2 前半（多格式导入）、Step 3 前半（项目对话记忆）、本会话完成勾接线。
> **以下是 plan 里还没做的（已核对当前代码），按建议优先级：**

1. **复盘 / review 视图（Step 3 后半，最顺的下一步）**：`GET /completions/heatmap` + 前端 `fetchHeatmap` 现成，本会话又把行内完成勾接上了 `completion_store`，数据在流——就差一个视图：commit 热力图墙 + 项目/周期复盘。**铁律：进度数字只来自 completion_store，LLM 只写叙述、不自报数字。**
2. **自然语言重排 UX（Step 4，半成品）**：确认卡片 `agentProposalModule` 已在；plan 里两点没做——(a) LLM 回复仍塞进 `state.statusMessage`（底部状态条、被 `lineLimit(2)` 截），没有**独立回复区**；(b) minor 改动的「乐观执行 + 撤销」没做（后端 **无 `POST /chat/agent/undo`**、`BlockChange` 无 `from_iso/to_iso/duration_minutes`、proposal 无 `expires_at`）。
3. **重复导入 = doc-diff + 指令 reconcile（Step 2 后半）**：二次导入改版文档仍可能重建；plan 是抽新文档 → 与现有任务按身份 diff → diff+用户指令喂 reconcile → 走完成感知 replan。
4. **今天日程写回日历（changeset UI）**：客户端 `applyEventChangeset`/`scheduleChangeset` + executor 都有，`SidebarView` 没接。
5. **收尾**：`integrations/caldav_client.py` 仍在（`legacy/caldav/` 未建，Step 5，等生成路彻底不依赖 CalDAV 再迁；注意 `do_sync_reminders` 仍是每日提醒来源）；**可分发 .app**（公证/图标/自动更新，也是 TCC 权限老重置的根因）；死代码清理（`startDocumentIntake`/`documentIntakeModule`）。

> 另：本会话发现的**非-plan 工程项**（想做可加进来）——聊天分步进度需把 `/chat/agent` SSE 化；Apply 提案静默失败的后端根因（`pending_proposals` 不持久 + confirm 不带 proposal_id）；任务间真依赖 A→B（需 `Subtask.depends_on`，用户暂缓）。

## 还没做（非阻塞）
- **今天日程写回日历**：`currentAgentEvents` → `POST /schedule/{date}/changeset` → `applyEventChangeset`（客户端+executor 都有，UI 没接）。
- **可分发 .app**：`make_app.sh` 只做本机 ad-hoc 验证；缺公证/图标/自动更新（也是权限老重置的根因）。
- **死代码清理**：旧假导入 `MockAssistantPanelState.startDocumentIntake` + `documentIntakeModule`；VM 里 chatbot 化后不再用的 `previewImport/confirmImport/importPreview/cancelImportPreview`。
- **提醒勾完成回报后端**：时间块那条已接 UI（2026-07-29）；系统「提醒」App 里勾完成仍回报不了（提醒 notes 只有 tag_key 短 hash，反推不出 block_key）。
- **移除 CalDAV → `legacy/caldav/`**：等生成路完全不依赖 CalDAV 再做；注意 `do_sync_reminders`（AppleScript 读本地提醒）仍是每日提醒的来源，动它前要有替代（前端上传当天到期提醒）。
- **重复导入 dedup**：chatbot 按 title 合并已大幅缓解；纯重复导入仍可能重建。
- **原生 tooltip 在悬浮窗弹不出**（§10.5）：若以后确实需要文字说明，得自建 hover 弹层，别指望 `.help()`。

## 怎么跑

```bash
# 后端（改 .py 后 uvicorn --reload 自动重载；跑前端时别狂改后端会掐断流）
.venv/bin/uvicorn main:app --reload

# 前端：改 Swift 后必须重新打包再开；别用 swift run（EventKit 权限过不了）
cd cal_swift_frontend && ./make_app.sh && open ScheduleAgent.app

# 测试（eval 会打真 LLM，日常跑排除它）
.venv/bin/python -m pytest -q --ignore=tests/eval      # 288 passed
```

## 关键文件

| 干什么 | 文件 |
|---|---|
| 项目对话 LLM（reply + 改后任务） | `agents/project_chat.py` |
| 项目服务（chat_about_plan / 多天 / import / replan / 快照） | `agents/project_service.py` |
| 多天规划器 | `agents/multiday_planner.py`、`models/planning.py` |
| 每日调度节点（窗口闸门 + 注入项目时段） | `agents/nodes.py` |
| 导入解析（文本/pdf/docx/图片识别+大小） | `integrations/document_parser.py`、`agents/plan_import_agent.py` |
| 存储（含 project_task/chat/multiday store + 迁移） | `storage.py`、`main.py`(lifespan) |
| 项目 API（chat/plan/multiday/import/replan） | `api/projects.py` |
| 前端：项目两栏详情（左对话右计划） | `cal_swift_frontend/.../ProjectDetailView.swift` |
| 前端：项目状态机 | `cal_swift_frontend/.../ProjectsViewModel.swift` |
| 前端：EventKit（读事件/提醒、本地日历、apply 变更集、按项目列表+色） | `cal_swift_frontend/.../AppleCalendarAdapter.swift` |
| 前端：后端客户端 + DTO | `cal_swift_frontend/.../DayflowAPIClient.swift` |
| 前端：主侧栏（启动/流/健康/重新生成） | `cal_swift_frontend/.../SidebarView.swift` |
| 打包 | `cal_swift_frontend/make_app.sh` |

## git

整个 Phase 4（含本会话：chatbot 项目、多天规划、图片视觉、窗口闸门、存储分离等）**都还没提交**。开工前建议先按 后端 / 前端 / 文档 分几个 commit 落一下。
