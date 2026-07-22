# 架构与运行时参考

> **这是什么:** 一份「现在到底怎么跑 + 为什么这么定 + 已知缺口」的活文档。
> 与 [phase3-plan.md](phase3-plan.md) 分工不同:那份是 *roadmap*(未来要做的);这份是 *how-it-works-now* + 决策记录 + 技术债。
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
- [ ] 纯函数 reconcile · 变更集接口 · `fetch_calendar` 改前端传入
- [x] 项目 replan → **提醒变更集**（`agents/reminder_reconcile.py` 纯函数 + `POST /projects/{id}/replan`，完成感知；replan 只出提醒清单，今天的时间块交日常路径刷新，返回 `affected_dates` 提示前端刷哪天）
- [ ] 导入 → 提醒变更集
- [ ] Swift EventKit 执行层
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
- **`energy_source`**(`models/schedule.py` `DaySchedule`):`today` | `baseline` | `none`,状态贯穿 state → DaySchedule → 前端。无数据 → `energy_curve=[]` + `source="none"`;排程走能量中性(solver/scheduler 回落到 0.5)。**Step 1 已完成;`baseline` 聚合还没建(见 phase3-plan §九)。**
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

杠杆(细节见 phase3-plan):prompt caching(最大,静态前缀被重发 ×N)、聊天回复流式(感知延迟)、砍聊天 `max_tokens`、减少轮数、模型分层。

---

## 8. 已知缺口 / 技术债(活清单)

- **死 fallback:** 老的结构化填槽路径(`agents/chat_agent.py` + `graphs/adjust_graph.py` + `POST /chat` 路由 + `AdjustState` + `visualize_graphs.py` 引用)仍接着线,但前端只调 `/chat/agent`(`agent_chat.ts:40`)→ 没人用。待意图地图定论后删(phase3-plan §九)。
- **`solve_schedule` 建了没接进 agent:** `agents/solver.py`(多日 solver + 结构化 relaxations)存在,但 ReAct agent 只有 `capacity_check`/`working_hours_until`,没有 solver。所以"超额时权衡取舍"—— 最强的 agent 用例 —— 现在跑不起来。
- **没有 deadline-vs-appointment 模型**(§4):instant 分类完全靠脆弱的关键词子串竞赛;同一个时间戳是"deadline"还是"do-at"取决于标志。**已选的下一条设计线。**
- **无 token 流式**(§7)。prompt caching 已给聊天 agent 开(轮内已验证),但 `task_agent`/生成路径、跨对话复用还没做。
- **`create_react_agent` 弃用警告**:langgraph V1.0 起已移到 `langchain.agents.create_agent`,V2.0 将移除;`agent_run.py` 仍用 `from langgraph.prebuilt import create_react_agent`(当前仅 warning,不影响运行)。
- **`agent_run_log` 只写不读:** 每个终止态都记(`agent_run.py:146-154`)但从没被读、不持久化、只存一个工具调用计数(不是完整 trace)。要么接进 eval,要么砍掉。
- **`subtask_overrides` 没持久化**,而 `subtask_pins` 持久化了 —— 大概率是疏漏;用户编辑过的拆解重启就丢。
- **eval 很薄:** 5 个手写场景(`tests/eval/`)、真 LLM、单次跑、all-or-nothing 通过率。没有数据集、没有和硬编码基线的对照、没有 pass@k、没有回归门。

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
| 前端 | `frontend/src/`(React/Vite)—— 另有实验性 Swift 客户端 `cal_swift_frontend/` |
