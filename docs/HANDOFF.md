# Phase 4 交接（2026-07-22）

> 明天接着干的清单。配合 [ARCHITECTURE.md](ARCHITECTURE.md)（§0 迁移状态、§8 技术债、§10 踩坑）一起看。
> 结论/踩坑记在 ARCHITECTURE；这份是「现在卡在哪、下一步做什么」。

## 现在能用的（本会话完成，已验证）

- **后端迁移三步全绿**（`pytest -q` → 265 passed）：
  1. 项目 replan → 提醒变更清单（`agents/reminder_reconcile.py` + `POST /projects/{id}/replan`，完成感知）
  2. `fetch_calendar` 支持前端传入日历（`ScheduleState.calendar_events`；`POST /schedule/generate` 带 `calendar_events`）
  3. 多格式导入（文本/.md/.txt/.pdf/.docx → 建 Task；`POST /projects/{id}/import`，dry_run 预览、意图闸门、日期平移）
- **EventKit 执行层**（`AppleCalendarAdapter.swift`）：读事件/提醒、apply 事件&提醒变更集（先删后建）、权限。**真机验证通过**（`open -W ScheduleAgent.app --args --verify-eventkit` → `~/eventkit-verify.log` 全绿）。
- **Projects 界面**（独立窗口，非浮窗）：列表/新建/删除、导入（chatbot 式一个输入区 + 回形针附件 + dry_run 预览确认）、日期平移说明、`重排并写入提醒`。
- **日期平移**（`agents/plan_reschedule.py`）：LLM 只读「第几周+周几」和解析说明，日期由纯函数算（换年 / 学期周锚点 / 周几覆盖）。两个日期字段都挪。
- **启动优先读缓存**：`onAppear` 先 `GET /schedule/{today}`（秒开、不碰 LLM/CalDAV），只有当天还没日程才走生成流。
- **健康数据**：后端一直有存盘（`data/health_store.json`）；启动回读睡眠窗口；SSE health 事件补了 `energy_source`；流断了回退拉缓存日程恢复曲线。

## 已修（2026-07-23 会话）

### P1 — 导入后「计划节点」空 → ✅ 改成「导入即写提醒」新流程
- **定案（跟用户确认过）**：导入不再只建任务。确认导入后：建 Task（存 `source_excerpt` 原文片段）→ 写计划快照（节点立刻可见）→ 返回提醒变更集，前端 EventKit 落地。不用再手点 replan。
- **reminder = 粗节点**：每条任务一个，保持大纲原始日期，**不拆子步骤**。细拆解留给「到期那天」的日常路径（`rank_and_decompose` 现在读 `source_excerpt` 给 LLM 上下文）。**不做 RAG**——单份大纲整份能进上下文，向量/检索是过度设计。
- **连带**：`replan_project` 也改成粗节点、**无 LLM 的重新同步**（改任务/勾完成后用）；前端按钮文案改「重新同步提醒」。
- 改动：`models/task.py`(+source_excerpt)、`agents/project_service.py`(`_task_as_node`/import_plan/replan_project)、`agents/task_agent.py`(payload+prompt)、`api/projects.py`(import 收 `current_reminders` JSON)、`DayflowAPIClient.swift`(importPlan 带 currentReminders + reminders DTO)、`ProjectsViewModel.confirmImport`(请求权限→读现有提醒→apply 变更集)。
- 测试：`tests/test_plan_import.py::test_import_writes_snapshot_and_reminder_changeset`、`test_projects_api.py::test_replan_endpoint`（改为粗节点断言）。

### P2 — 生成仍读 CalDAV → ✅ 新增 POST-SSE，前端上传本地日历
- **定案**：真流式 POST-SSE。新增 **`POST /schedule/stream`**（body 带 `calendar_events`，复用 `stream_schedule_events`）；GET SSE 带不了 body 的坑绕过了。
- 前端：`streamSchedule(date:calendarEvents:)` 有本地日历就 POST、没有退回 GET；`AppleCalendarAdapter.localCalendarEvents(on:)` 读当天全部本地事件；生成前 `requestEventAccess` 只要日历权限。`loadDayflowSchedule(generate:true)`（睡眠输入后重排）也接上了。
- 测试：`tests/test_calendar_frontend_input.py::test_post_stream_endpoint_uses_supplied_calendar`。
- **仍待真机验证**：GUI 里首次生成会弹日历权限；授权后确认 uvicorn 日志不再出现 CalDAV 世界杯赌博日历。

## 打开的问题

- **导入写提醒的 GUI 端到端还没真机验证**：EventKit executor + POST-SSE 都单测过，但 GUI 里「确认导入 → 弹提醒权限 → 提醒事项 App 出现待办」整条链没在真机跑过。注意首次要授权提醒。
- **重复导入会产生重复任务/提醒**（已知、延后）：每次导入建新 uuid 任务 → 新 block_key → `current_reminders` 对不上、不会去重。要做「文档 diff + 指令 reconcile」才能解，见下方延后清单。

## 还没做（迁移/功能，非阻塞）

- **今天日程写回日历**：生成后 `currentAgentEvents` → `POST /schedule/{date}/changeset` → `applyEventChangeset`（客户端 + executor 都有，UI 没接）。
- **热力图墙 / 复盘**界面（`GET /completions/heatmap` 已就绪）。
- **可分发 .app**：`make_app.sh` 只做本机 ad-hoc 签名验证用；缺公证/图标/自动更新。
- **死代码清理**：旧假导入 `MockAssistantPanelState.startDocumentIntake` + `documentIntakeModule`（入口已换成 Projects，点不到）。
- **提醒勾完成回报后端**：提醒 notes 里只有 tag_key 短 hash，反推不出 block_key；时间块完成走 `setBlockCompletion` 那条能用。
- **移除 CalDAV → `legacy/caldav/`**（迁移最后一步，等 EventKit 生成路跑通再做）。
- **图片/截图导入**：走 Claude 视觉（不是 OCR），延后。
- **重复导入 reconcile**（文档 diff + 指令）：延后。

## 怎么跑

```bash
# 后端（改后端 .py 后 uvicorn --reload 自动重载；跑前端时别一直改后端，会掐断 SSE）
.venv/bin/uvicorn main:app --reload

# 前端：改 Swift 后必须重新打包再开；别用 swift run（EventKit 权限过不了，见 ARCHITECTURE §10）
cd cal_swift_frontend && ./make_app.sh && open ScheduleAgent.app

# EventKit 无头验证
open -W ScheduleAgent.app --args --verify-eventkit && cat ~/eventkit-verify.log

# 测试
.venv/bin/python -m pytest -q      # 265 passed
```

## 关键文件（前端 Phase 4）

| 干什么 | 文件 |
|---|---|
| 后端客户端（含 Phase 4 接口 + DTO） | `cal_swift_frontend/.../DayflowAPIClient.swift` |
| EventKit 执行层 + tag 解析 | `cal_swift_frontend/.../AppleCalendarAdapter.swift` |
| Projects 状态机（列表/导入/replan） | `cal_swift_frontend/.../ProjectsViewModel.swift` |
| Projects 列表 / 详情 | `cal_swift_frontend/.../ProjectsView.swift`、`ProjectDetailView.swift` |
| 独立窗口 | `cal_swift_frontend/.../ProjectsWindowController.swift` |
| 主侧栏（启动/流/健康） | `cal_swift_frontend/.../SidebarView.swift` |
| 无头验证入口 | `cal_swift_frontend/.../EventKitVerification.swift`、`ScheduleAgentApp.swift`(AppEntry) |
| 打包脚本 / Info.plist | `cal_swift_frontend/make_app.sh`、`Info.plist` |

后端关键：`agents/project_service.py`、`agents/reminder_reconcile.py`、`agents/plan_reschedule.py`、`agents/plan_import_agent.py`、`integrations/document_parser.py`、`api/projects.py`、`graphs/schedule_stream.py`。

## git

整个 Phase 4（后端迁移 + 前端 API/执行层/界面 + 真机验证）**都还没提交**。明天开工前可以先按后端/前端分几个 commit 落一下。
