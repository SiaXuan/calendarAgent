# Phase 4 交接（2026-07-23）

> 明天接着干的清单。配合 [ARCHITECTURE.md](ARCHITECTURE.md)（§0 迁移状态最全、§10 踩坑）一起看。
> 结论/踩坑记在 ARCHITECTURE；这份是「现在到哪了、下一步做什么」。
> 测试：`.venv/bin/python -m pytest -q` → **276 passed**。

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

## 下一步（明天做，用户主诉求）

### 每日动态重排 + 结转记忆（Step 1.6 续）
- **多天计划只是「确认未来放得下」**；实际**只落地/sync 当天**，第二天按最新情况重新规划。
- **结转记忆**：用户说「作业1 学习资料还没看完」→ 模型记住未完成部分，**第二天日程自动接上「继续昨天没做完的 X」**。
- 涉及：完成/未完成状态回流 + 接 LangMem（或复用 `project_chat_store` 的对话记忆）+ 每日重规划触发点。先想清楚「当天 sync 什么、第二天重算什么、未完成怎么结转」的数据流。

## 待真机验证（都单测过，GUI 端到端没跑）
- 项目对话整条链：贴大纲/截图 → 右边出节点 → 说「拆细/挪一挪」→ 节点变 → 「排入多天日程」→ 「写入」提醒。
- 首次「写入」要授权提醒；首次「排入多天/生成」要授权日历。
- 确认 uvicorn 日志不再联网读 CalDAV（世界杯赌博日历）。

## 还没做（非阻塞）
- **今天日程写回日历**：`currentAgentEvents` → `POST /schedule/{date}/changeset` → `applyEventChangeset`（客户端+executor 都有，UI 没接）。
- **热力图墙 / 复盘**（`GET /completions/heatmap` 就绪）。
- **可分发 .app**：`make_app.sh` 只做本机 ad-hoc 验证；缺公证/图标/自动更新（也是权限老重置的根因）。
- **死代码清理**：旧假导入 `MockAssistantPanelState.startDocumentIntake` + `documentIntakeModule`；VM 里 chatbot 化后不再用的 `previewImport/confirmImport/importPreview/cancelImportPreview`。
- **提醒勾完成回报后端**：提醒 notes 只有 tag_key 短 hash，反推不出 block_key。
- **移除 CalDAV → `legacy/caldav/`**：等生成路完全不依赖 CalDAV 再做；注意 `do_sync_reminders`（AppleScript 读本地提醒）仍是每日提醒的来源，动它前要有替代（前端上传当天到期提醒）。
- **重复导入 dedup**：chatbot 按 title 合并已大幅缓解；纯重复导入仍可能重建。

## 怎么跑

```bash
# 后端（改 .py 后 uvicorn --reload 自动重载；跑前端时别狂改后端会掐断流）
.venv/bin/uvicorn main:app --reload

# 前端：改 Swift 后必须重新打包再开；别用 swift run（EventKit 权限过不了）
cd cal_swift_frontend && ./make_app.sh && open ScheduleAgent.app

# 测试
.venv/bin/python -m pytest -q      # 276 passed
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
