# 全局路线图（大框架 + 落地进度）

> **这是什么**：项目的**全局蓝图 + 哪些做了哪些没做**，长期稳定、不随单次会话变。
> 三份文档分工，别混：
> - **本文件 ROADMAP** = 要去哪 / 做到哪了（全局，长期）。
> - **[ARCHITECTURE.md](ARCHITECTURE.md)** = 现在到底怎么跑 + 为什么这么定 + 已知技术债（作者的项目「日记本」）。
> - **[HANDOFF.md](HANDOFF.md)** = 只写**当前这次**交接，每次会被下次覆盖，不含全局。
>
> **状态图例**：✅ 已完成 · 🔨 部分 · 🔭 未开始（远景）。每次落地就来这里改对应行。
> 详细设计原稿（含取舍推演）在 `~/.claude/plans/`（见文末），本文件是它们的**权威汇总**——以本文件为准，原稿仅供查动机。

---

## 定位（远景）

从「单一排程工具」升级为**可扩展的个性化健康规划框架**。核心 IP 不只是 health + cognitive load，而是：
- **参考健康研究/论文**，把生理数据系统性地用进日程决策（抽象掉具体数据源与规则）。
- **角色/职业语境感知 + 长期学习**：主框架通用，每个用户能带自己的 MCP（笔记/知识库/代码库），agent 越用越懂自己。

三层模型：**Profile 层**（用户/职业特定：user memory + 自带 MCP + profile 子图）→ **Core Schedule Graph**（LangGraph 主图）→ **Data + Rules 层**（通用 HealthSnapshot + 多 adapter + 研究驱动规则引擎）。

---

## 两条计划线

项目有两条并行的计划线，别只盯其中一条：

### 线 A — 大框架五阶段（`md-phase3-polished-rocket.md`）

| Phase | 内容 | 状态 | 备注（核对代码 2026-07-29） |
|---|---|---|---|
| **A** | LangGraph 迁移 + `task_kind` 维度 + agent/workflow 分层 | ✅ | `graphs/*`（schedule_graph / agent_run / adjust_graph / state）、`agents/llm.py`、scratch + `classify_impact` + 乐观并发都在；`task_kind`(analytical/insight/admin) 已入模型 |
| **B** | 多源健康数据（Apple Health / Oura / WHOOP / Garmin，抽象 `HealthAdapter` + `HEALTH_SOURCE`）| 🔭 | **未开始**，设计上刻意降级。现在只有**手动睡眠输入**，无 `integrations/health/`。远景路线：B.1 Health Auto Export webhook → B.2 Open Wearables MCP → B.3 adapter 抽象层 |
| **C** | LangMem 长期记忆 + 学习闭环 | 🔨 | **部分**：C.1 storage / C.2 Inspector / C.3 规则触发写入+读取 已做（`memory/store.py`·`observations.py`·`retrieval.py`，仅 `schedule_prefs` namespace 通电；`task_lexicon`/`physiological`/`interactions` 已定义未通电）。**C.4（每周 LLM 反思 + 衰减 cron）未做**（无 `memory_extractor`）。Embedding 依赖决策留 C.5 |
| **D** | 用户自带 MCP 扩展接口 | 🔭 | **未开始**。无 `graphs/user_mcp.py`、无 `custom_mcp_servers`/`MCPConfig`。`chronotype` 目前只是 `health_agent` 按就寝时间算的中间值，不是 `UserPreferences` 可配字段/下拉。明确不做：MEQ 问卷 / role 类目 / 预定义 task_lexicon |
| **E** | 研究论文驱动的健康规则引擎 | 🔭 | **未开始**。无 `agents/health_rules.py`。现在能量曲线是 `health_agent` 里的高斯规则（晨/午后/傍晚峰 + chronotype + 午后谷 + 睡眠修正），**不是**带论文引用、可在 health card 展开看依据的规则集。起步 5 条（基于 Daniel Pink《When》）见原稿 |

**推进顺序（已定）**：A → C → D → E 主线；**B 显式降级**为远景（手动输入够用，等有「用」的需求再启动，且 B.2 必须等 A）。每个 Phase 独立可验证、可做完停下来用一段时间。

### 线 B — EventKit + 项目层（`whimsical-seeking-shannon.md` Step 0–4 + `parsed-gliding-platypus.md`）

即 2026-07-21 的**纯本地 + EventKit 转向**：Swift 前端用 EventKit 承担所有系统日历/提醒读写，后端纯逻辑只返回 `{create,update,delete}` 变更集，不碰 iCloud/CalDAV。决策记录见 ARCHITECTURE §0。

| Step | 内容 | 状态 |
|---|---|---|
| **0** | 写入层加固（block 级 diff + 事务性 + 结构化错误）→ 重构成纯函数 reconcile | ✅ |
| **1** | 项目数据层 + 完成追踪 + 完成感知 reconcile（`completion_store`、`GET /completions/heatmap`）| ✅ |
| **1.6** | 自动多天规划（每日生成时增量纳入）+ 结转记忆（过去未完成上浮「继续：」）| ✅ |
| **2 前** | 多格式导入 → 项目 Task（文本/pdf/docx/图片视觉）| ✅ |
| **2 后** | **重复导入 = doc-diff + 用户指令 reconcile**（二次导入改版文档不重建）| 🔭 未做 |
| **3 前** | 项目 = 一条有记忆的对话（chatbot，`project_chat_store`）| ✅ |
| **3 后** | **复盘 / review 视图**（commit 热力图墙 + 项目/周期复盘；后端 `heatmap` 就绪，前端 `fetchHeatmap` 就绪，**缺视图**）| 🔨 数据已通，缺 UI |
| **4** | 自然语言重排 UX：确认卡片 ✅；**独立回复区** + **minor 乐观执行+撤销**（`POST /chat/agent/undo`）| 🔨 半成品 |
| **5** | 收尾：`integrations/caldav_client.py` → `legacy/caldav/`；今天日程写回日历 changeset UI；可分发/公证 .app；死代码清理 | 🔭 未做 |

**完成态数据链已打通**（2026-07-29）：侧栏行内完成勾 → `POST .../complete` → `completion_store` → 喂 heatmap。所以线 B 最顺的下一步是 **Step 3 后半：复盘视图**。

---

## 贯穿铁律（跨两条线都成立）

- **自主性只住在 Agent 层**，且只在「情况会变 + 该走不同链路 + 没 hardcode」处；Layer 0 确定性引擎故意零自主（日常主力要可靠）。
- **影响面分级 gate 是确定性的、在 agent 控制之外**：agent 只在 scratch 副本改，产 diff → `classify_impact` 判 minor/major（agent 碰不到）→ minor 原子提交 / major 出 Proposal 等确认。外发/不可逆动作（写日历/发邮件）无论大小都走确认门。
- **进度数字只来自 `completion_store`（确定性代码），LLM 只写叙述、绝不自报数字**。
- **日期算术交确定性代码，LLM 只读语义**（见 ARCHITECTURE §10.4）。
- **逻辑留后端，系统 I/O（日历/提醒）交原生 Swift/EventKit**；不再引 iCloud/CalDAV/App 专用密码。

---

## 明确不做

- 不一次做完所有 Phase；不重写 `scheduler_agent` 的纯逻辑；不把「会议管理/调度链接/多源 task 聚合」塞进 A–E（另一个产品方向）。
- 不引 PostgresStore（先 InMemoryStore + JSON）；不做权限隔离/多用户（保持单用户假设，架构留 hook）。
- Phase D 不做 MEQ 问卷 / role 类目 / 复杂职业 profile 页。

---

## 详细设计原稿（外部，仅供查动机）

在 `~/.claude/plans/`（不在 git，是 plan-mode 产物）：
- `md-phase3-polished-rocket.md` — 线 A 大框架全设计（三层模型、agent/workflow 重构、Phase A–E 明细、5 条健康规则样例）。
- `whimsical-seeking-shannon.md` — 线 B Step 0–4 全设计。
- `parsed-gliding-platypus.md` — 线 B Step 1.6（自动多天 + 结转）。

> 已删：`docs/phase3-plan.md`（旧 roadmap，内容已并入本文件）。别再找它。
