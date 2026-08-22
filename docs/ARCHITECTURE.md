# 架构与运行时参考

> **这是什么:** 一份「现在到底怎么跑 + 为什么这么定 + 已知缺口」的活文档。
> 与 [ROADMAP.md](ROADMAP.md) 分工不同:那份是 *全局蓝图 + 进度*(要去哪);这份是 *how-it-works-now* + 决策记录 + 技术债。当前交接看 [HANDOFF.md](HANDOFF.md)。
>
> **维护约定:** 每次落地实现或改设计,同步更新对应章节 **和** 技术债清单(§8)。写 `file:line` 时先读代码确认,不凭记忆。
>
> **以下事实核验于 2026-07-16。** 行号会漂,只当锚点,依赖前先重新确认。

> ⚠️ **2026-07-21 架构转向(见 §0):本项目改为纯本地 + EventKit,不再走 iCloud CalDAV。**
> 下方 §1–§9 大部分描述的是**转向前**的 CalDAV 现状,会随迁移逐步更新。以 §0 为准。

---

## 0. 架构转向:纯本地 · 仿原生 · EventKit(决策记录,2026-07-21)

### 定位
一个**纯本地、仿原生的 macOS 日程助理 app**。日历与提醒读写全部走本地系统能力,
**完全不依赖 iCloud / 网络**,也不打算云端化。

### 为什么改(以前怎么做 → 为什么不行)
- **以前**:后端用 **CalDAV 联网到 iCloud** 读写日历事件;提醒因为 iCloud 的 CalDAV
  `/reminders/` 通道被苹果封锁(PROPFIND 400),只能用 **AppleScript 读本地提醒**,而**写提醒从未实现**。
- **为什么不行**:
  1. iCloud 对第三方**提醒**基本不可用(连读都封),而本项目核心场景之一是「把项目节点写成待办」——CalDAV 路线做不出来。
  2. 定位是纯本地仿原生,绕 iCloud 又慢、又要 App 专用密码、又不原生。
  3. **实测(2026-07-21,用户 Mac)**:AppleScript 本地建/读/删「提醒」和「日历事件」全部成功;本地已有可写的 `Agent` 日历。本地路线可行。

### 现在怎么做(新路线)
- **Swift 前端(EventKit)** 承担**所有**系统日历/提醒读写:读事件与空档、写/改/删事件、读写提醒、勾选完成、申请权限。
- **Python 后端 = 纯逻辑,永不碰系统日历**:拆解、排程、项目层、完成追踪、reconcile 策略、进度/复盘。存储仍是本地 JSON(`data/`)。
- **数据流**:前端读本地日历 →上传「当天固定事件 + 当前 agent 事件现状」→ 后端排程 + 完成感知 reconcile → 返回**变更集 `{create, update, delete}`**(事件 + 提醒)→ 前端用 EventKit 执行 → 回报完成态。
- **diff 在后端算,前端只执行**(已与用户确认)。逻辑全留后端,前端是"哑"执行器。
- **身份/tag**(嵌在事件/提醒 notes 里):活动 `[agent-scheduled:dayflow:<hash>]` + `[agent-project:<pid>]`;历史 `[agent-history:dayflow:<hash>]` + `[agent-project:<pid>]` + `[agent-done:<iso>]`。`<hash>` = block_key(`{task_id}::{title}`) 的短 hash(避免标题里的 `]` 破坏匹配)。
- **项目未来节点 = 提醒**:导入/拆解的未来节点写成带到期日 + 项目 tag 的**提醒**,不是时间块;到那天由现有「提醒→任务→排程」流水线排成当天时间块。
- **保留 Step 0 的 reconcile 策略**(未变不动/删除先行/绝不重复/跳过已完成),现在体现为后端产出的变更集(前端先删后建),而非后端直接联网。

### 经验教训
- iCloud CalDAV 对第三方**提醒**是死路,别再试。
- 系统日历/提醒的正规本地接口是 **EventKit**(Swift);AppleScript 能用但慢,仅作应急/参考(实测脚本见 git 历史)。
- 正确边界:**逻辑留后端,系统 I/O 交原生前端**。

### 文件去向(迁移映射)
旧的 iCloud/CalDAV 实现**移到 `legacy/caldav/`**(不删,便于日后扩展新技术路径时复用,如真要做跨设备同步)。

| 旧文件 | 去向 | 说明 |
|---|---|---|
| `integrations/caldav_client.py` | `legacy/caldav/` | 事件 CalDAV 读写 + 提醒 AppleScript 读,整体停用 |
| `agents/calendar_writeback.py` 的 CalDAV 调用 | 重构为纯函数 `agents/calendar_reconcile.py` | block-diff **策略**保留为纯变更集计算 |
| `fetch_calendar` 读日历 | 改为接收前端传入的日历数据 | 后端不再联网读日历 |

> 迁移未完成前旧文件暂留原位以免破坏测试;新路径跑通并验证后即按上表移动,并把本表更新为「已完成」。

### 迁移状态
- [x] 数据模型 / 存储层 / Project CRUD / 完成追踪 / heatmap
- [x] Step 0 block-diff 策略(待重构为纯变更集)
- [x] 纯函数 reconcile · 变更集接口（`calendar_reconcile.reconcile_schedule` + `POST /schedule/{date}/changeset`）· `fetch_calendar` 改前端传入（`ScheduleState.calendar_events`；`fetch_calendar_node` 收到就用纯函数算、不联网，`None` 才降级读 CalDAV；`POST /schedule/generate` 加 `calendar_events`。CalDAV 仍作兜底，第 5 步才删）
- [x] 项目 replan → **提醒变更集**（`agents/reminder_reconcile.py` 纯函数 + `POST /projects/{id}/replan`，完成感知；replan 只出提醒清单，今天的时间块交日常路径刷新，返回 `affected_dates` 提示前端刷哪天）
- [x] 多格式导入（文本/.md/.txt/.pdf/.docx）→ 建项目 Task（`integrations/document_parser.py` + `agents/plan_import_agent.py` + `models/plan_import.py` + `POST /projects/{id}/import`，支持 dry_run 预览、意图闸门 is_plan/confidence≥0.55、超限/非计划 422）。图片/视觉与「重复导入 doc-diff+指令 reconcile」留后续。
- [x] **项目任务与调度池分离（2026-07-23，关键修正）**：import 之前直接写全局 `task_store`（= 每日调度池，`rank_tasks_node` 全量读它），导致**没确认的项目节点被排进今天**。改为项目任务存 **`project_task_store`**（`storage.py`，独立落盘），全局 `task_store` 只留「要现在排的」（ad-hoc + reminder-synced）。`project_tasks()` 读 project 存储；`import_plan`/`delete_project` 写它。启动时 `_migrate_project_tasks_out_of_task_store()` 把历史遗留的带 `project_id` 任务从 task_store 迁出（幂等）。未来「多天规划器」负责把项目节点「提拔」进每日调度。`GET /plan` 用 `get_or_build_plan`：没快照但有项目任务时现建（老项目也能显示节点）。
- [x] **导入→复核→写入 两步流程（2026-07-23 定案，覆盖了更早的「导入即写」）**：导入只**建任务（进 project_task_store）+ 写计划快照**（项目页「计划节点」立刻可复核）；用户复核后点**「写入日历」**按钮才通过 EventKit 写 reminder。**reminder = 粗节点（每条任务一个，保持大纲原始日期），不做子步骤拆解**；细拆解留给「到期那天」的日常路径，靠任务上存的 `source_excerpt`（原文片段）给 LLM 上下文。`replan`（= 写入日历按钮调的）是粗节点、**无 LLM 的重新同步**。不做 RAG：单份大纲能整份进上下文，向量/检索是过度设计。相关：`project_service.import_plan/_task_as_node/replan_project`、`task_agent.rank_and_decompose`（payload 加 `source_excerpt`）、`Task.source_excerpt`。
- [x] **reminder 按项目分组 + 颜色（2026-07-23）**：写入时每个项目一个**提醒列表**（EKCalendar，名=项目名、色=`Project.color` 用户自选 hex）。`AppleCalendarAdapter.applyReminderChangeset(listName:colorHex:)` + `reminderCalendar(named:colorHex:)`；`Project.color` 字段 + `ProjectDetailView` 里 ColorPicker。
- [x] **窗口闸门（2026-07-23，语义已校正）**：窗口 = **只有 deadline 落在今天+`SCHEDULE_HORIZON_DAYS`（默认 5 天）内的**任务才进排程（过期/无日期的普通提醒也进）。远 deadline（几个月后的作业）**现在完全不排**，等 deadline 临近才进。`agents/nodes.py::_within_horizon` 管 task_store 每日提醒；`project_service._in_window_project_nodes` 管项目节点。
- [x] **多天调度模型（Step 1.6，2026-07-23）**：`agents/multiday_planner.py` — 推理 LLM（`sonnet`，可 `LLM_REASON_MODEL` 换 opus）+ 贪心兜底 + 容量/deadline 校验。**只规划窗口内 deadline 的项目节点**，把它们摊到 [今天, deadline] 里逐步做完。**关键：不是把任务切成同名等长 block**——LLM 读 `source_excerpt`/描述想清楚任务要哪几个**阶段步骤**（读材料→实现→检查→提交…），每步一个有意义的标题 + 时长（30–120 分钟，一步一坐不打断），能收口就一天收口。输出 `PlannedChunk`（`task_title` 父任务 + `title` 具体步骤），存 `multiday_plan_store`（按 project_id）。每日调度 `rank_tasks_node` 注入当天 `chunk_subtasks_for_date`。端点 `POST /projects/plan-multiday`、`GET /projects/{id}/multiday`。前端：ProjectDetailView「排入多天日程」按钮 + 按天分布展示（步骤+父任务）；容量由 `fixedMinutesByDate` 读未来 30 天本地日历算。贪心兜底只能出「第 k/n 段」通用标题（LLM 失败时）。
- [x] **项目 = 一条有记忆的对话（2026-07-23）**：项目详情页改成两栏——左多轮对话、右计划节点（标题+日期）。`project_chat_store`（按 project_id 存全历史，落盘）+ `agents/project_chat.converse`（sonnet，reply + 可选改后任务列表）+ `project_service.chat_about_plan`（应用改动：同名任务保 id→进度/提醒不丢，重建快照）。**导入并进对话、无硬意图闸门**：`POST /projects/{id}/chat` 是 multipart（message + 可选 file：图走视觉、文档 parse 成文本），会话 LLM 自己判断——是计划就抽进右边，不明确就反问兜底（「你发的像 X，想…吗？」），绝不硬拒。旧 `POST /import` + 置信度闸门仍在（阈值放宽到 0.4、去掉 is_plan 一票否决）但前端 chatbot 不再走它。前端 `submitComposer` 一律走 `/chat`。
- [x] **每日动态重排 + 结转记忆（Step 1.6 续，2026-07-24，用户主诉求）**：
  - **触发点搬进每日生成（关键纠正）**：多天规划**不再是项目页手动按钮**，改为每日生成日程前**自动增量**跑。
    `project_service.ensure_multiday_plan(anchor_date, fixed_minutes_by_date, force=False)`：先剪枝（任务已删/全完成的 chunk），
    再找**在窗口内但 store 里还没有任何 chunk 的**任务（=新滚进 5 天窗口的节点）跑 LLM，**只排新节点、不重跑已排的**；
    容量按 `effective_fixed = 前端固定分钟 + 已排 chunk 分钟`扣减，新节点排在剩余空档、不双重占用。`anchor_date`=生成的目标日期，
    「到了新的一天」= 窗口自然前移、新节点入窗。`POST /schedule/generate` 和 `POST /schedule/stream` 跑图前先调它
    （`GenerateRequest` 加 `fixed_minutes_by_date`；前端 generate/stream 带 `fixedMinutesByDate(from:days:30)`）。
    旧 `POST /projects/plan-multiday` 保留但改成 `force=True` 全量重排（dev/覆盖后门，前端不再调；已删项目页按钮 + VM.planMultiday）。
  - **结转叠加层**：`multiday_plan_store`=计划底稿（结转不改它），`completion_store`=完成叠加层。
    `chunk_subtasks_for_date(target)` 改为注入「`c.date ≤ target` 且未勾完成」的 chunk——**过去未完成的自动上浮到今天**，
    标 `carried_over=True`（Subtask/TimeBlock 新字段，scheduler 透传；前端 `displayTitle` 加「继续：」前缀）。
    **结转块保持原 title 不变** → `block_key={task_id}::{title}` 稳定 → 勾一次完成即收口，不无限结转。容量溢出交现有 scheduler 进 `unscheduled`。
  - **对话可补充**：`project_chat.PlanChatResult` 加 `progress:[{task_title,status:done|in_progress}]`；用户在项目对话说
    「X 做完了 / 还没做完」→ `project_service._apply_chat_progress` 给该任务的 chunk 写/清完成记录（done→停止结转+进 heatmap）。
  - **不引 LangMem**：复用 `completion_store` + `project_chat_store` 已够，不做向量检索；不加 chunk 级 remaining_minutes（v1 按整 chunk 结转）。
  - 单测 `tests/test_carryover.py`（10）+ 改 `test_multiday_planner`/`test_project_chat` 各 1 处断言（返回字段变化）。286 passed。
- [x] Swift EventKit 执行层（`AppleCalendarAdapter`：读事件/提醒、apply 事件&提醒变更集、`localCalendarEvents(on:)` 读当天本地日历供生成用；真机验证通过）
- [x] **生成走本地 EventKit（P2，2026-07-23）**：GET SSE 带不了 body，新增 **`POST /schedule/stream`**（真流式 POST-SSE，body 带 `calendar_events`）。前端 `streamSchedule(date:calendarEvents:)` 有本地日历就 POST、没有就退回 GET。规避了世界杯赌博日历那种 CalDAV 联网读。
  - **坑（改后即修）**：读本地日历要按**日历类型**排除订阅流——`.subscription`（中国/加拿大节假日、节气、世界杯赛程…）和 `.birthday` 是信息流不是个人占用；再叠加过滤 `isAllDay`。只按 `isAllDay` 不够（节气事件不一定标全天，用户报「大暑」仍占满全天）。见 `AppleCalendarAdapter.localCalendarEvents`。
  - **坑（改后即修）**：**生成绝不能在中途 `await` 日历权限弹窗**——一开始把 `requestEventAccess()` 放进 `loadDayflowSchedule(generate:true)`（睡眠输入后重排那条），权限弹窗把整个重新生成挂住，能量曲线永远不刷新（`applyManualSleepWindow` 乐观设了 `energySource=.today` 但不设曲线，所以表现为「有睡眠标签、曲线空白、无报错」）。改为：`onAppear` fire-and-forget 请求一次权限；生成只用 `hasEventAccess`（非弹窗检查）已授权才读本地日历，否则降级。
- [x] **前端完成态接线（2026-07-29）**：Upcoming / Today Queue 行内加「完成勾」→ `POST /schedule/{date}/blocks/{block_key}/complete` → `completion_store`（喂 `GET /completions/heatmap` 复盘）。block 读取时 join 出的 `is_done`（`models/schedule.py:35`）现在前端也解码进 `DayflowScheduleBlock.isDone` → 任务 metadata。**sync 与 done 拆成两个图标**：日历图标（`calendar.badge.plus/.checkmark`）=写系统日历，✓ 圈=完成。`setBlockCompletion`/`fetchHeatmap` 客户端方法之前就在（`DayflowAPIClient.swift`），这次才接 UI。`toggleTaskDone` 乐观更新、失败回滚、不锁面板。**复盘/heatmap 视图仍未做**（见 §8、HANDOFF）。
- [ ] 移除 CalDAV 文件到 `legacy/caldav/`

### 给后来 agent 的提示
- **不要**再引入 `caldav` / iCloud / App 专用密码相关代码或依赖。
- 读写系统日历/提醒走 Swift/EventKit(前端)或后端变更集接口,**不要**在 Python 里直接联网。
- `legacy/caldav/` 是**存档参考**,不是活跃实现。

---

## 1. 系统是两半

| | 确定性 workflow | 动态 agent |
|---|---|---|
| **在哪** | `graphs/schedule_graph.py` | `graphs/agent_run.py` |
| **干什么** | 每日生成日程 | 对话式调整 |
| **形状** | 静态 LangGraph DAG(固定边) | ReAct 循环(`create_react_agent`)—— 形状运行时才定 |
| **自主性** | 零,故意的(日常主力要可靠) | LLM 自己决定调哪个工具、调几次、什么顺序 |
| **怎么看** | `scripts/visualize_graphs.py`(能画出来) | LangSmith trace / `agent_run_log`(**画不出来**,它是个循环) |

**关键:** 生成流程能画成一张图;聊天 agent **画不出**固定的图,因为它是运行时循环。这是理解整个 codebase 最重要的一点。

### 生成 DAG 拓扑(`schedule_graph.py:60-97`)
```
START → fetch_health → fetch_calendar → compute_meals ┐
                     └→ rank_tasks → split_instant ────┴→ apply_pins → schedule → assemble → END
```
- `fetch_health` 先单独跑(便宜、规则化),好让 SSE 立刻发出去(`schedule_graph.py:73-77`)。
- `fetch_calendar` ‖ `rank_tasks` 并行 fan out(super-step 2)。
- `apply_pins` 在 `compute_meals` 和 `split_instant` 都完成后汇合。
- 尾段(`apply_pins → schedule → assemble`)顺序执行、纯规则。

---

## 2. 模型分路(`agents/llm.py`)

| 客户端 | 模型(默认) | temp | max_tokens | 用在哪 |
|---|---|---|---|---|
| `sonnet` | `claude-sonnet-4-6` | 0.3 | 4096 | 聊天 ReAct agent(`agent_run.py:220`)、任务拆解(`task_agent.py:230`)、旧 chat(`chat_agent.py:82`)、task chat(`task_chat_agent.py:86`) |
| `haiku` | `claude-haiku-4-5-20251001` | 0.0 | 2048 | **仅**非英文健康摘要翻译(`health_agent.py:179`) |

env 可覆盖:`LLM_FAST_MODEL` / `LLM_REASON_MODEL` / `LLM_FAST_TEMPERATURE` / `LLM_REASON_TEMPERATURE`(`agents/llm.py:33-45`)。两个客户端都没设 `streaming=` / `timeout=` / `max_retries=`。

---

## 3. 聊天 agent 运行时(`graphs/agent_run.py`)

全项目唯一有真运行时自主性的地方。

- **循环:** `create_react_agent(sonnet, tools, prompt=system_prompt)`(`:220`),由阻塞的 `await agent.ainvoke(...)`(`:223`)驱动。
- **工具带(8 个):**
  - 日程工具(`agents/tools/schedule_tools.py`):`get_schedule`、`move_block`、`remove_block`、`add_fixed_event`、`capacity_check`、`working_hours_until`
  - 信号工具(`agent_run.py:91-105`):`ask_user`(→ clarification)、`report_blocked`(→ degraded)
- **轮数:** `_RECURSION_LIMIT = 16`(`:50`)≈ 8 轮 模型↔工具 上限(模型步+工具步 = 每轮 2 步)。**这是天花板,不是脚本** —— 典型一次 2-4 轮,简单的 1 轮。每轮是一次*阻塞、串行*的 sonnet 往返,而且那坨大 system prompt(渲染后的日程 + memory bullets + 规则 + 工具 schema + 最多 12 条历史)**每轮都重发**(没缓存,见 §7)。
- **5 个终止态**(`:56`,判定在 `:246-294`):
  - `clarification` —— LLM 调了 `ask_user`(LLM 决定)
  - `degraded` —— LLM 调了 `report_blocked`,或循环超限 / 异常(兜底)
  - `no_change` —— diff 为空(确定性)
  - `success` —— diff 经 gate 判 `minor` → 原子提交(确定性)
  - `proposal` —— diff 经 gate 判 `major` → 暂存,等确认(确定性)
- **安全壳(LLM 够不到):**
  - agent 只改 `ScheduleScratch` 副本,绝不碰 live `schedule_store`。
  - `classify_impact(diff)`(`agents/scratch.py`,在 `:270` 调用)是**确定性 gate** —— agent 无法说服自己把 major 改动偷偷自动提交。
  - 原子提交 + 版本号 bump;陈旧 Proposal 保护靠 `base_version` + TTL `_PROPOSAL_TTL_MIN = 5`(`:51`);`confirm_proposal`(`:297`)。
- **多轮:** `chat_sessions` 带最多 `_MAX_HISTORY_MSGS = 12`(`:52`)条历史;有新的 pending proposal 时后续对话叠在它上面细化。

---

## 4. 任务分类(`is_instant`)

- **入库时由硬编码关键词规则 `_detect_instant` 决定**(`api/tasks.py:59-64`):标题含触发词(`api/tasks.py:20-28`:`提交`/`交`/`pay`/`submit`/`email`…)**且不含**排除词(`api/tasks.py:32-40`:`课程`/`报告`/`作业`/`report`/`assignment`…)。
- **LLM 被禁止标 instant。** system prompt 要求全部设 `is_instant=false`(`task_agent.py:54-63`);instant 任务根本不进 LLM;LLM 若标了 `is_instant=true` 会被强制改回 `False`(`task_agent.py:253`)。拆解器信任存下的标志 + 一个时长下限 `estimated_hours <= 0.1`(`task_agent.py:129-140`)。
- **没有 deadline-vs-appointment 区分。** 只有单个 `deadline`/`deadline_dt` 字段(`models/task.py:41-42`)。regular 任务把它当软 deadline(排序/紧急度,从不钉死开始时间);instant 任务把*同一个*时间戳当硬"就这点做"的开始时间(`nodes.py:81`)。用哪种解释完全由 instant 标志(= 关键词命中)翻转。**已知缺口,见 §8。**

---

## 5. 健康 / 能量

- **单维** `HealthSnapshot`(`models/health.py`):睡眠 + 静息心率 + HRV + 步数 + active_minutes。无 recovery/stress 分数(那是 Phase B,已降级)。
- **能量曲线纯规则**(`agents/health_agent.py` `compute_energy_curve`):3 个高斯峰(晨/午后/傍晚)+ chronotype 系数(由就寝时间推)+ 午后谷(13-15h ×0.8)+ 睡眠时长/HRV/活动修正。不是 Phase E 的研究规则引擎(那个还不存在)。
- **`energy_source`**(`models/schedule.py` `DaySchedule`):`today` | `baseline` | `none`,状态贯穿 state → DaySchedule → 前端。无数据 → `energy_curve=[]` + `source="none"`;排程走能量中性(solver/scheduler 回落到 0.5)。**Step 1 已完成;`baseline` 聚合还没建。**
- 摄入:仅手动(`POST /health`)。iPhone Shortcuts 路径已删;未来 Apple Health 入口在 `api/health.py` 里注释留好。

---

## 6. 存储 / 状态(14 个容器)

除标注外都在 `storage.py`。持久化是机会性 JSON 快照(每个改数据的 helper 自己调 `save_*`)。

**持久化:**
- `health_store` → `data/health_store.json`
- `task_store` → `data/task_store.json`
- `schedule_store` + `schedule_version` + `subtask_pins` → `data/schedule_store.json`(一个文件)
- `memory_store` → `data/memory_store.json`
- `preferences`(`api/preferences.py`)→ `data/preferences.json`
- `subtask_cache` → `data/subtask_cache.json`(每日拆解缓存,按 `task_id`+内容 hash;稳定 block_key,见 §10.7)

**临时(内存,重启即失):**
- `subtask_overrides` —— ⚠️ **没**持久化,而 `subtask_pins` 持久化了(不一致 —— §8)
- `observation_log` —— pre-memory 信号,故意临时
- `agent_run_log` —— 只写不读(§8)
- `pending_proposals`、`chat_sessions` —— 会话状态
- `_health_cache`、`_calendar_cache`(`agents/nodes.py:40-41`)—— 派生缓存;`_calendar_cache` 有 300s TTL

学习闭环:`observation_log` →(同模式 ≥3 次信号)→ 提升进 `memory_store`(`memory/observations.py`);检索把 `confidence>0.6` 的记忆注入 task/chat prompt(`memory/retrieval.py`)。目前只有 `schedule_prefs` namespace 有活写入路径;`task_lexicon`/`physiological`/`interactions` 已定义但未通电。

---

## 7. 延迟特性

- **prompt caching:聊天 agent 已开(轮内)。** `agent_run.py` 把 system prompt 包成 `cache_control: {type: ephemeral}` 块(`:218` 附近),Anthropic 缓存覆盖到断点为止的前缀(= tools + system),一次对话第 2..N 轮直接读缓存。实测:第 2 次调用 3023/3357 input token 命中缓存(2026-07-16)。**尚未覆盖** `task_agent` / 生成路径;跨对话只部分命中(易变的日程快照嵌在 system prompt 中段)。
- **全项目没有 token 流式。** 聊天路径(`POST /chat/agent`)是纯阻塞 `ainvoke` —— 整个循环跑完前客户端一个字收不到。`GET /schedule/stream/{date}` 是 SSE 但**节点粒度**(`graph.astream(stream_mode="updates")`,`schedule_stream.py`),不是 token 级。
- **聊天 = 最多 ~8 次串行、未缓存的 sonnet 往返**(依赖式循环 —— 无法并行)。
- **生成 = sonnet 拆解(`rank_tasks`)在关键路径上**,只和 CalDAV I/O 并行(不和 haiku 健康调用并行,后者在 super-step 1)。
- 没配 retry/timeout;失败回落到启发式或 degraded。

杠杆:prompt caching(最大,静态前缀被重发 ×N)、聊天回复流式(感知延迟)、砍聊天 `max_tokens`、减少轮数、模型分层。

---

## 8. 已知缺口 / 技术债(活清单)

- **死 fallback:** 老的结构化填槽路径(`agents/chat_agent.py` + `graphs/adjust_graph.py` + `POST /chat` 路由 + `AdjustState` + `visualize_graphs.py` 引用)仍接着线,但前端只调 `/chat/agent`(`agent_chat.ts:40`)→ 没人用。待意图地图定论后删。
- **`solve_schedule` 建了没接进 agent:** `agents/solver.py`(多日 solver + 结构化 relaxations)存在,但 ReAct agent 只有 `capacity_check`/`working_hours_until`,没有 solver。所以"超额时权衡取舍"—— 最强的 agent 用例 —— 现在跑不起来。
- **没有 deadline-vs-appointment 模型**(§4):instant 分类完全靠脆弱的关键词子串竞赛;同一个时间戳是"deadline"还是"do-at"取决于标志。**已选的下一条设计线。**
- **无 token 流式**(§7)。prompt caching 已给聊天 agent 开(轮内已验证),但 `task_agent`/生成路径、跨对话复用还没做。
- **`create_react_agent` 弃用警告**:langgraph V1.0 起已移到 `langchain.agents.create_agent`,V2.0 将移除;`agent_run.py` 仍用 `from langgraph.prebuilt import create_react_agent`(当前仅 warning,不影响运行)。
- **`agent_run_log` 已落盘、带结果标签(2026-08-20):** 每次 chat agent run + accept/reject 结果写 `data/agent_run_log.jsonl`(gitignored;pytest 下只在内存不落盘,见 `storage.append_agent_run_log` 的 `PYTEST_CURRENT_TEST` guard)。run 记录含 input + 冻结日程快照 + 能量曲线 + diff changes + 工具调用序列 + impact + 延迟;outcome 记录用 `proposal_id` 关联 applied/rejected/expired/stale(`graphs/agent_run.py` 的 `_log`/`_log_outcome`,reject 走 `POST /chat/agent/dismiss`)。**目的是攒真实 case 做 eval 数据集**——但导出成 `tests/eval` fixture 的脚本 + 合成扩量还没做(见 ROADMAP「Eval / 数据集」)。
- **`subtask_overrides` 没持久化**,而 `subtask_pins` 持久化了 —— 大概率是疏漏;用户编辑过的拆解重启就丢。
- **eval 很薄:** 5 个手写场景(`tests/eval/`)、真 LLM、单次跑、all-or-nothing 通过率。没有数据集、没有和硬编码基线的对照、没有 pass@k、没有回归门。**真实 case 采集已就位**(见上条埋点),差「导出成 fixture + 合成扩量」——见 ROADMAP「Eval / 数据集」。
- **前端死代码(Phase 4):** 旧的假导入流程 `MockAssistantPanelState.startDocumentIntake` + `documentIntakeModule` 还在,但入口("Add Document" 磁贴)已被 "Projects" 取代 → 点不到。真导入走 `ProjectsView`/`ProjectDetailView`。待清。
- **提醒勾完成没回报后端:** 时间块完成的那条路**已接 UI**(2026-07-29,侧栏行内完成勾 → `POST /schedule/{date}/blocks/{block_key}/complete`)。但用户在系统「提醒」App 里勾掉一条 agent 提醒时,前端仍无法回报 —— 提醒 notes 里只有 tag_key 的短 hash,反推不出 block_key。要么在提醒 notes 里也塞明文 block_key,要么完成态只认时间块那条路。
- **任务先后依赖只做了「同父任务阶段」(2026-07-29):** `scheduler_agent.generate_schedule` 现在把同一父任务的子任务成组、按拆解顺序处理,并给后一阶段一个 `min_start` 下限(前一阶段结束+buffer),避免「先做练习、再做备考计划」这种反序(回归测试 `tests/test_scheduler_agent.py::test_same_task_phases_keep_order`;`test_independent_tasks_still_energy_ranked` 锁住独立任务仍按能量排)。聊天调整 agent 的 prompt 也加了「保持先后」约束(`graphs/agent_run.py`)。**但两个独立任务之间的 A→B 依赖仍无数据模型**(`Subtask` 无 `depends_on`)—— 真要做得加字段并让 scheduler/solver 尊重它。`_priority_of` 桩函数已删(排序改按 parent_rank/parent_order/phase)。
- **Apply 提案静默失败已缓解、根因未除(2026-07-29):** `pending_proposals` 纯内存(`storage.py`)+ 乐观并发(`base_version`)+ 5min TTL → 后端 `--reload` 或日程在确认前变动都会让 `confirm_proposal` 返回非 `success`。前端过去在非 success 时**什么都不做**(表现为「点 Apply 没反应」);已改为**清掉失效提案卡 + 状态栏显示原因 +(若后端回了日程)刷新最新日程**(`SidebarView.confirmAgentProposal`)。根因(提案不持久 + confirm 只带 date、不带 proposal_id,`api/chat.py`)未动。
- **没有可分发的 .app:** `cal_swift_frontend/make_app.sh` 只是把 `swift build` 产物包成 ad-hoc 签名的 `.app` 供本机 EventKit 验证(见 §10)。没有正式打包/公证/图标/自动更新。
- **导入靠真 LLM,偶发形状问题已兜:** `ExtractedPlan` 的 `with_structured_output` 偶尔把嵌套 list/object 返回成 JSON 字符串,已用 `field_validator(mode="before")` 兜(见 §10);导入端点其余异常降级为 502 而非 500。但没有对导入抽取的 eval/回归。

---

## 9. 从哪看起(入口)

| 领域 | 文件 |
|---|---|
| 生成流程 | `graphs/schedule_graph.py`、`agents/nodes.py` |
| 聊天 agent | `graphs/agent_run.py`、`agents/tools/schedule_tools.py`、`agents/scratch.py` |
| 模型/客户端 | `agents/llm.py` |
| 任务摄入 + 分类 | `api/tasks.py`、`agents/task_agent.py` |
| 健康/能量 | `agents/health_agent.py`、`api/health.py` |
| 记忆 | `memory/store.py`、`memory/observations.py`、`memory/retrieval.py` |
| 存储 | `storage.py` |
| Solver(未接进 agent) | `agents/solver.py`、`agents/calc.py` |
| API 路由 | `api/*.py`(`main.py` 挂载) |
| 前端(**活跃**) | `cal_swift_frontend/`(原生 Swift):`SidebarView.swift` 主界面、`DayflowAPIClient.swift` 后端客户端、`AppleCalendarAdapter.swift` EventKit 执行层、`ProjectsView`/`ProjectDetailView`/`ProjectsViewModel` 项目+导入界面 |
| 前端(旧) | `frontend/src/`(React/Vite)—— 逐步被 Swift 客户端取代 |

---

## 10. Phase 4 踩坑与经验(前端 EventKit + 多格式导入,2026-07-22)

> 这些是实际卡住过、花时间才弄对的点。后来 agent 碰到相关改动前先读这节,省得重踩。

### 10.1 EventKit 权限:必须是真 `.app` 包 + 用 `open` 启动
macOS 的隐私系统(TCC)对日历/提醒授权极挑剔,踩坑顺序:
1. **裸 `swift run` 二进制** → 请求直接被拒(denied),连授权框都不弹 —— 因为没有用途字符串。
2. **给二进制内嵌 Info.plist**(Package.swift 用 linker `-sectcreate __TEXT __info_plist`)→ 有了 bundle id 和用途字符串,但**还是拒**;`tccutil reset ... <bundleid>` 报 "No such bundle identifier"。TCC 不认裸 Mach-O。
3. **打成真正的 `.app` 包 + ad-hoc 签名**(`make_app.sh`:组 `Contents/{MacOS,Info.plist}` + `codesign --sign -`)→ `codesign -dv` 显示 "app bundle"。但**直接跑包里的二进制仍被拒** —— 请求被算到父进程(终端)头上。
4. **`open -W ScheduleAgent.app --args ...`**(经 LaunchServices 启动)→ app 成为自己的"responsible process",**这才弹授权框、授权成功**。✅

**结论/配方:** 改完 Swift → `./make_app.sh` → `open ScheduleAgent.app`(GUI)或 `open -W ScheduleAgent.app --args --verify-eventkit`(无头验证,输出写 `~/eventkit-verify.log`,因为 `open` 拉起的进程 stdout 不回终端)。**别用 `swift run` 碰 EventKit。** 用途字符串 `NSCalendars/RemindersFullAccessUsageDescription` 在 `cal_swift_frontend/Info.plist`。
Swift 6 严格并发副作用:持有 `EKEventStore` 的 `AppleCalendarAdapter` 要标 `@unchecked Sendable`(才能从 `@MainActor` 传给 nonisolated 方法);EventKit 异步回调里 `EKReminder` 非 Sendable,用 `nonisolated(unsafe)` 局部变量过检查。

### 10.2 Claude 结构化输出会把嵌套字段返回成 JSON 字符串
`sonnet.with_structured_output(ExtractedPlan)` **偶发**(非必现)把嵌套的 `candidate_tasks`(list)/`project_meta`(object)整个序列化成一段 JSON **字符串**而不是原生值 → Pydantic 校验炸 → 500。
**修法:** 在模型上加 `@field_validator("candidate_tasks","project_meta","adjustment", mode="before")`,是 `str` 就先 `json.loads`(见 `models/plan_import.py`)。**任何用 with_structured_output 的嵌套模型都该防这一手。** 另外导入端点把非 `DocumentParseError` 的异常降级成 502 友好错误,不再甩裸 500。

### 10.3 PDF 表格:用 pypdf 的 layout 模式,别上 OCR
课程表/PRD 这种**表格型 PDF**,pypdf 默认 `extract_text()` 会把列拆散、顺序打乱,抽出来是乱码 → LLM 判"不是计划"直接拒。**修法:** `page.extract_text(extraction_mode="layout")`(保留列对齐),逐页兜异常(`integrations/document_parser.py`)。
**扫描件/截图(无文字层)不要上 OCR** —— 在本项目的 LLM 架构里 OCR 是错的工具;正解是把图片喂 **Claude 视觉**(同一个 `ExtractedPlan` 结构)。**已实现(2026-07-23)**：`plan_import_agent.extract_plan_from_image` + `document_parser.image_mime`/`check_size`,导入识别图片扩展名即走视觉；前端导入框 `onPasteCommand` + 图片按钮粘贴截图。

### 10.4 日期算术交确定性代码,LLM 只读语义
复用旧 syllabus 到新学期(如 25→27 年、"第一周顺排、作业固定周几交、课改周三")这类需求:**不要让 LLM 算日历日期**(它会算错"第 N 个周一是几号""某日是星期几")。分工:
- **LLM 读结构**:每个任务的 `week_index`(第几周)+ `due_weekday`(周几);把用户自然语言说明解析成 `ImportAdjustment`(target_year / term_start_date / due_weekday / shift_weeks)。**明确禁止 LLM 输出平移后的日期。**
- **确定性代码算日期**:`agents/plan_reschedule.py::apply_adjustment` 两种模型(学期周锚点 / 换年保持"某月第 N 个周几"),纯函数、pytest 全覆盖。
这条和项目既有铁律一致(进度数字来自 completion_store、不让 LLM 自报)。将来若要在聊天里做"整体挪一周",把这个纯函数包成 agent 工具即可,别让 agent 一步步算。

### 10.5 悬浮侧栏是 `.floating` 非-key 窗口,三个坑(2026-07-29)
侧栏活在一个 `.floating`、透明、从不成为 key 的 `NSWindow`(`ScheduleAgentApp.swift` `WindowConfigurator`/`SidebarWindowController`),App 平时也不 active。由此三个坑:
1. **异步 `@State` 更新不上屏,要等事件泵 run loop。** 流式能量曲线/任务卡、睡眠输入后的曲线重算、完成勾——静止 hover 时都「不动」,重新 hover 那下的展开动画才把积压更新一次性刷出来。**修法:** 侧栏展开时挂一个 30Hz `Timer`(`.common` 模式)`displayIfNeeded()` 泵 run loop(`SidebarWindowController.setDisplayPump`,`setExpanded` 里开关)。收起即停。
2. **原生 `.help()` tooltip 被系统抑制、弹不出来。** 非-key 悬浮窗里 AppKit tooltip 不显示。**决定:** 不做自定义 tooltip 层,靠图标自解释(sync 用日历图标并和「Sync All」同图标;done 用 ✓ 圈)。`.help(...)` 代码保留但别指望它显示。
3. **Upcoming 栏卡片色是「反相」的。** `upcomingModuleFill`/`upcomingPrimaryColor`(`SidebarView.swift`):**浅色系统里画成深色卡、深色系统里画成浅色卡**。所以行内控件(± / 完成圈)的字色必须跟 `upcomingPrimaryColor`(= 那栏标题色,新增 `controlGlyphColor` 指向它),**不能用原始 `colorScheme` 判断**,否则「浅色模式→黑字」落在这栏的深卡上就看不见。「底深字浅、底浅字深」的规则要对着**这栏的实际底色**、不是系统外观。

### 10.6 Swift 前端设计约定（从旧 `PROJECT_MEMORY.md` 收编，2026-07-29）
Swift 是**第二前端**,只管 macOS UI / 本地视图态 / API 调用 / 解码 / 交互;**不重实现**后端排程/LLM/健康/记忆/拆解逻辑。视觉参照 macOS 原生 widget / Control Center(不是 web dashboard):悬浮窗透明、无阴影,视觉重量在卡片上。已定的 UI 约定:
- **颜色语义**:绿色**只**给「当前进行中」的任务/事件(绿标题+时间+卡下绿色进度线,**不用 NOW pill**);**green 绝不用作 synced 态**(读起来像 done,冲突)。synced-to-calendar 用安静的蓝(细线勾 / 淡蓝描边)。
- **badge**:任务类型 badge 放第二行(和时长/session 元数据同行),**别挤标题**;长标题可小一号、最多两行。徽章用后端 `task_kind`(有则显 `ANALYTICAL`/`INSIGHT`…)、否则回退 `cognitive_load`;**颜色**编码认知负荷强度(见 §10.5 的 `controlGlyphColor` 讨论),不是用户优先级。
- **Upcoming = 主工作时间轴**(旧「Today Queue」语义已并入 = scheduled 的 upcoming agent 块);fixed / meal 锚点比 agent 任务更安静。
- **拖拽重排**:整条 Upcoming 时间轴可拖(不只拖到另一张卡上),y 坐标→时间、吸附 15 分钟、拖放显横线+时间徽章;冲突交**后端 `/pin` reflow**,不做本地碰撞;synced / fixed 不可拖。工作日映射当前 8:00–22:00(`ScheduleDragTargetResolver`)。
- **番茄/时长**:单次 `1 x 25m · 25+5m`、多次 `N x 25m · TOTALm`;± 本地即时更新 + 后端 debounce,调 `/schedule/{date}/pin`(同 `block_key` + 原 start + 新时长)。
- **能量态**:`energy_source==none` 时**不画假曲线**,显示空态/CTA(「Add sleep input」);手动睡眠输入始终可用,编辑发后端(不在 Swift 维护第二真相源)。
- **代理提案**:agent 说「要删/移」但后端返回 proposal(需确认)时,**不得静默改 UI**;保留后端 proposal/confirm 语义。

### 10.7 block_key 稳定化 + 相关一串修复(2026-08-22)
一批真机暴露的问题,根子和收尾:

- **`block_key` 会漂 → pin/complete/结转 重排后失效(重启才好)= 已修。** 块的逻辑身份是 `{task_id}::{title}`,但 `task_id` 只标"任务"、一个任务拆成多个子任务块共享它,所以早期靠拼**子任务标题**区分。而每日提醒/临时任务走 `rank_tasks_node` 每次生成都**重调 LLM 拆解、标题换词** → key 每次变 → 前端手里的旧 key 重排后全失配(pin 404、complete 静默记错);重启只是重新拉齐 key。**修法(不改 key 格式、零迁移):** 新增持久化 `subtask_cache`(按 `task_id`+内容 hash),`nodes._decompose_with_cache` 在任务内容未变时**复用上次拆好的子任务**、只对新增/改动任务调 LLM → 标题稳 → key 稳。memory 故意不进 hash(否则记忆一变就重拆,破坏稳定)。项目 chunk 那条本就稳(标题落盘)。回归测试 `tests/test_subtask_cache.py`。
- **认知负荷分类器债已还。** `api/tasks._llm_classify_batch` 从「裸 `anthropic` 客户端 + 手写 `json.loads`(被 ```json 围栏搞崩)」改为共享 `haiku` 的 `with_structured_output`,**优先 `json_schema`(Claude 原生结构化输出,Haiku 4.5 支持)、失败降级 `function_calling`**。Anthropic 的"JSON mode"就是结构化输出(`output_config.format`),LangChain `with_structured_output(method=...)` 可选。注意默认 `sonnet=claude-sonnet-4-6` 不在 json_schema 支持列表,故只改了用 haiku 的分类器。
- **空/默认名提醒过滤(带鲁棒性)。** `api/tasks._reminder_effective_title`:标题是默认名(「新提醒事项」/「New Reminder」等)且备注也空 → 跳过;标题占位但**备注有内容 → 用备注首行当标题**(不丢用户真填了内容的提醒)。
- **删除卡片。** `POST /schedule/{date}/blocks/{block_key}/remove` 从今日 `schedule_store` 删块 + 清 pin + bump 版本(不 reflow、不删底层任务);前端 '−' 减到只剩 1 个番茄再按 → 弹确认框 → 调它。
- **跨天自动前移。** 前端 `scheduleDate` 原来启动算一次不动,挂机过夜停在昨天。现在监听 `NSCalendarDayChanged`(午夜)+ `NSWorkspace.didWake`(睡眠跨午夜补一刀)→ `advanceDayIfNeeded` 推日期 + 重载(跑结转)。见 `SidebarView`。
