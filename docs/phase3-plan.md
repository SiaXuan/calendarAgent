# 重新定位：从单一工具到可扩展的个性化健康规划框架

## Context

用户经过几轮探讨澄清了**远景**，决定走 LangGraph 路线。三个深层动机：

1. **当前流程短≠任务简单**：现在 schedule pipeline 只有几步，是因为还没把全局视野延伸开。希望框架能支持未来加长链路 —— 比如把"健康研究 → 决策规则"显式建模进 graph
2. **核心 IP 不止 health + cognitive load**：真正的目标是**参考健康研究 / 论文**，把生理数据系统性地用到日程决策上。所以需要**抽象掉具体数据源**（不只 Apple Health，还有 Oura/WHOOP/Garmin/Fitbit）、**抽象掉具体规则**（让规则可配置、可被研究文献驱动）
3. **角色 / 职业语境感知 + 长期学习**：不同职业的人对同一句任务描述（"准备 lecture"）有不同的认知负荷和时长。用户希望：
   - 主框架是通用的 LangGraph 链路
   - 每个用户能**带自己的 MCP**（自己的笔记、code repo、临床记录、行业知识库）
   - **长期学习用户习惯**，让 agent 越用越懂自己

**为什么这套必须用 LangGraph**：
- 主图 + 子图 + 检索"个性化语境"的能力 → LangGraph 的多 graph 组合是核心特性
- 模型 agnostic → 用户哪天想换 Llama / GPT 不绑定 Anthropic
- LangMem 原生支持长期记忆 + 学习闭环
- LangSmith 给"为什么 agent 做了这个决定"的可追溯性 —— 对健康决策至关重要

**这不是单纯的技术迁移，是产品定位升级。** 当前项目作为载体落地，架构上为远景留接口。

---

## 一、新架构：三层模型

```
┌──────────────────────────────────────────────────────────────┐
│   Layer 3: Profile Layer (用户/职业特定)                       │
│   ─ user_memory (LangMem, namespace=user_id)                  │
│   ─ user_mcp_servers (用户自带的 MCP — 如 Notion/Obsidian/...)│
│   ─ profile_graph (子图: 翻译"准备 lecture"→ cognitive_load等) │
└──────────────────┬───────────────────────────────────────────┘
                   │ 主图按需调用
┌──────────────────▼───────────────────────────────────────────┐
│   Layer 2: Core Schedule Graph (LangGraph)                    │
│   ─ fetch_health → fetch_calendar → rank_tasks → schedule    │
│   ─ 调用 health_rules_engine (Layer 1 的规则)                  │
│   ─ 写入 LangMem (学习用户反馈)                                │
└──────────────────┬───────────────────────────────────────────┘
                   │ 标准化接口
┌──────────────────▼───────────────────────────────────────────┐
│   Layer 1: Data + Rules (跨用户通用)                           │
│   ─ HealthSnapshot 通用 schema                                │
│   ─ 多 adapter: Apple Health / Oura / WHOOP / Garmin / ...   │
│   ─ Health rules engine: 研究文献驱动的规则集                  │
└──────────────────────────────────────────────────────────────┘
```

**关键设计**：用户能在 Layer 3 注入自己的 graph 节点 + MCP，主图 (Layer 2) 用标准协议调用 —— 这就是用户问的"带自己 MCP 进来"的落地。

---

## 二、分阶段实施路线（5 个 Phase）

**实际推进顺序**（用户已确认）：
- **A → C → D → E** 是主线
- **B（多维健康数据）已显式降级**为远景 —— 现状的手动输入足够，等到有"用"的需求再启动
- 每个 phase 独立可验证、独立有价值，可以做完一个停下来用一段时间

### Phase A —— LangGraph 基础迁移 + Task 模型扩展 (~2-2.5 周)

**目标**：把现有手写 orchestrator 换成 LangGraph state graph，行为基本等价；**顺手**加 `task_kind` 维度因为反正要改 Task 流转的代码。

**A.1 — LangGraph 迁移（主体）**
- 安装 `langgraph` / `langchain-anthropic` / `langchain-core`
- 抽离 `storage.py`（持久化）
- `agents/llm.py` 集中 ChatAnthropic 客户端
- `graphs/state.py` 定义 TypedDict
- `graphs/schedule_graph.py` 主图：fetch_health / fetch_calendar / rank_tasks 并行 → meal → scheduler → persist
- `graphs/adjust_graph.py` chat 调整图
- `graphs/task_chat_graph.py` per-task 多轮
- 所有原生 anthropic SDK 调用 → `ChatAnthropic.with_structured_output(Pydantic)`
- FastAPI 路由内部 invoke graph
- 替换 SSE 为 `graph.astream_events`
- LangSmith 接入

**A.2 — Task 模型加 `task_kind` 维度（基于 Daniel Pink《When》）**

理由：cognitive_load 只表示"强度"（deep/medium/light），但**同样的 deep 任务有不同最佳时段**。Pink 的研究指出：

| task_kind | 含义 | 黄金时段（third_bird/lark）| 黄金时段（owl）|
|---|---|---|---|
| `analytical` | 分析、专注、问题求解（写代码、读论文）| 早晨 | 傍晚 |
| `insight` | 创造性、跳跃联想、灵感（构思、设计）| 下午/傍晚 | 早晨/傍晚 |
| `admin` | 程序性、低专注（邮件、整理、报销）| afternoon trough 13-15 | 任意 |

改动：
- [models/task.py](models/task.py) Task / Subtask 加 `task_kind: TaskKind` 字段（默认 `analytical` 以保持现有行为）
- `task_agent` 在 prompt 里要求 Claude 输出 task_kind
- `scheduler_agent` 调度时 `task_kind × chronotype × hour` 决定 energy_threshold（先简单实现，后期 Phase E 规则化）
- 前端 ScheduleTimeline 在 cognitive_load 旁显示 task_kind 小标签

引用：[Daniel Pink on timing impacts productivity](https://www.rolandberger.com/en/Insights/Publications/Daniel-Pink-on-how-timing-impacts-productivity.html)（time of day 解释 20% performance variance）

**完成标志**：前端体验基本不变；LangSmith dashboard 能看到完整 trace；Task 多了 task_kind 字段且 Claude 输出对了 ~90% 准确率（通过看几个例子人工评判）。

### Phase B —— 多维健康数据接入（远景，非紧急）

**当前现状**（v1，足够用）：
- iPhone Shortcuts 路径**正式放弃** —— 网络/防火墙/DHCP 坑太多，长期不稳定
- 前端 [SleepInputModal](frontend/src/components/SleepInputModal.tsx) 手动输入睡眠时长 + 能量曲线为唯一输入方式
- 这是"够用"的最小可用状态，不阻塞后续 Phase A/C/D 的核心架构演进

**远景目标**：参考**多维健康数据**形成有差异化的调度决策。具体路线（待后续展开 / 暂不立项）：

#### B.1 — Health Auto Export webhook（中期，单设备最佳实践）
- $5 iOS app，业界事实标准的 Apple Health 自动导出
- 新增 `POST /health/import-auto-export` endpoint 接 webhook
- 跟现有手动输入并存，作为"自动补全"路径
- 工作量：半天 - 一天
- 优势：稳定、自动、支持 100+ metrics
- 引用：[HealthyApps 集成文档](https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/)

#### B.2 — Open Wearables MCP（长期，多设备统一）
- 2026 年初由 Apple Health MCP Server 演化为自托管 platform
- **统一接入 Apple Health + Garmin + Polar + Suunto + Whoop**
- 通过 MCP 协议暴露
- 前提：Phase A LangGraph 完成（用 `langchain-mcp-adapters` 接入零代码）
- 工作量：1-2 天（接入），数据维度的研究价值无穷
- 引用：[Open Wearables 介绍](https://www.themomentum.ai/blog/apple-health-mcp-server-by-momentum)

#### B.3 — 健康数据 abstraction 层（配合 B.1 / B.2）
- 新建 `integrations/health/` + 基类 `HealthAdapter`
- 实现：`ManualInputAdapter`（当前现状）、`HealthAutoExportAdapter`（B.1）、`OpenWearablesMCPAdapter`（B.2）
- `HealthSnapshot` schema 增强：`recovery_score`、`stress_score`、`activity_load`、`temperature_baseline`、`menstrual_phase` 等多维字段（设备能给的填，给不了的 None）
- `.env` 配 `HEALTH_SOURCE=manual|auto_export|open_wearables`

#### Phase B 优先级

**显式降级**：在 Phase A/C/D 完成之前不要主动启动 Phase B。原因：
- 现状（手动输入）够用
- 多维健康数据的价值要在 Phase C（LangMem 学习闭环）+ Phase D（行业 profile）+ Phase E（研究规则）都到位后才能完全发挥 —— 否则就是"拿到更多数据但不会用"
- B.2（Open Wearables）必须等 Phase A 完成

**触发条件**：当你（或其他用户）开始觉得"手动输入太烦了 / 想看更深的健康-决策关联"时再启动。

### Phase C —— LangMem 长期记忆 + 学习闭环 (~1-1.5 周)

**目标**：让 agent **跨日学习**用户习惯，但同时**避免表现退化**（agent 学到错的东西后越来越差的死循环）。

#### C.0 — 该学什么 / 不该学什么

| 学什么（语义化、长期价值高）| 不学什么 |
|---|---|
| ✅ 抽象模式："prefer analytical work mornings"（不是 "rejected block on Tuesday"）| ❌ 原始 schedule events（已在 schedule_store）|
| ✅ 量化偏好："typical lunch duration: 45min ± 10"（带方差）| ❌ 健康原始数据（已在 health_store）|
| ✅ 任务词典：'compile bibliography' → admin, ~30min | ❌ 每次 accept/dismiss（噪声大，存抽象模式而非事件）|
| ✅ 生理基线："resting HR baseline: 58bpm" | ❌ 临时性内容（"今天加班"不是长期事实）|
| ✅ 用户在 chat 里明确说的偏好 | ❌ confidence < 0.5 的猜测 |

**判定原则**："如果 3 个月后这条还有用，存；如果一周就过期，不存。"

#### C.1 — Memory schema

```python
class Memory(BaseModel):
    id: str
    namespace: tuple[str, ...]         # ("default", "schedule_prefs")
    content: str                       # 自然语言事实
    structured: dict | None            # 可选结构化形式 e.g. {"hour": 9, "action": "reject"}
    confidence: float                  # 0-1
    source_event_ids: list[str]        # 哪些事件导出来的
    created_at: datetime
    last_reinforced_at: datetime       # 衰减算法用
    decay_rate: float = 0.05           # weekly decay if not reinforced
    user_verified: bool = False        # 用户在 inspector 里确认过吗
```

**4 个 namespace bucket**：
- `("default", "schedule_prefs")` —— 时段、任务类型偏好
- `("default", "task_lexicon")` —— 任务描述 → 含义（feeds 进 Phase D 的 profile_graph）
- `("default", "physiological")` —— HRV/HR/sleep 个人基线（feeds 进 Phase E 的健康规则）
- `("default", "interactions")` —— episodic log（带 TTL 30 天，定期清理）

#### C.2 — 两路写入策略

**路径 A：规则触发（确定性高的离散信号）**
```python
# 当累计 N 次同方向信号才升级为 memory
if user_rejected_block_at_hour_X >= 3 within 7_days:
    memory.add("user typically rejects blocks at hour X", confidence=0.7)
```
适用：accept/dismiss、drag、complete/skip。

**路径 B：LLM 反思（每周一次组合多维信号）**
```python
class WeeklyInsight(BaseModel):
    pattern: str
    confidence: float
    evidence_summary: str
    namespace: Literal["schedule_prefs", "task_lexicon", "physiological"]
```
适用：跨维度组合模式（单个动作推不出的高阶规律）。

**关键约束**：单次反馈**不立即写**，先进 observation 池，等满 N 次再升级 —— 避免"用户某天心情不好拒绝 5 个 block 就被记成'讨厌 deep work'"。

#### C.3 — 读取模式（注入到哪些 prompt）

| 读取节点 | Query 模板 | 注入到哪 |
|---|---|---|
| `rank_tasks_node` | `"scheduling preferences for {weekday} with tasks: {titles}"` | task_agent system prompt |
| `apply_health_rules_node`（Phase E）| `"user's physiological baselines"` | health rules engine（替代 population default）|
| `chat_agent.handle_message` | `"user habits relevant to: {user_message}"` | chat_agent context |
| `profile_graph`（Phase D）| `"how user typically handles '{task_title}'"` | task_kind/load 估算 |

只读 `confidence > 0.6` 的，避免被弱信号污染。

#### C.4 — 退化防护（最危险的部分，必做）

| 失败模式 | 对策 |
|---|---|
| **早期偏差锁定** | 前 2 周写入 confidence 砍半；N-observation gate |
| **矛盾记忆共存** | 写入时**检测冲突**（同 namespace + 相似 structured），提示用户裁决 |
| **记忆漂移** | 每周衰减：未被新观察强化的 memory，`confidence -= 0.05`；< 0.3 移到 archive |
| **过度自信** | calibration：跟踪 memory 预测的准确率，反向 fit confidence |
| **黑盒不可控** | **Memory Inspector 前端页**：列出所有 memory + confidence + 编辑/删除 |

**必做**：**Memory Inspector 必须先于规则/LLM 写入路径完成**。否则你看不到系统记了什么 → 退化时无法调试 → 放弃功能。

#### C.5 — Embedding 依赖决策

LangMem 默认用 OpenAI text-embedding-3-small。`.env` 留 `EMBEDDING_PROVIDER=openai|voyage|local` 开关：

| 选项 | 隐私 | 质量 | 建议场景 |
|---|---|---|---|
| OpenAI | ❌ 出网 | 标杆 | 默认，最方便 |
| VoyageAI | ❌ 出网 | 很好 | 想留 Anthropic 生态 |
| sentence-transformers（本地）| ✅ 完全本地 | ~90% | 隐私优先 |

#### C.6 — Phase C 拆 4 步执行

| 步骤 | 内容 | 时间 |
|---|---|---|
| **C.1 storage** | LangMem InMemoryStore + namespace + JSON snapshot 持久化 | 半天 |
| **C.2 Inspector UI** | Settings 页加 "Memory" tab：列表 + 编辑 / 删除 | **必须先于写入做** | 1 天 |
| **C.3 规则触发写入 + 读取** | accept/dismiss N 次 → memory；retrieve in task_agent + chat_agent | 2 天 |
| **C.4 LLM 反思 + 衰减** | weekly batch + 衰减定时任务 | 1-2 天 |

#### 跟现有代码的接入点

| 现有代码 | 改动 |
|---|---|
| [ScheduleTimeline.tsx](frontend/src/components/ScheduleTimeline.tsx) `accepted` / `dismissed` localStorage | 改成 POST `/memory/feedback` 同时本地保留 |
| [agents/task_agent.py](agents/task_agent.py) `rank_and_decompose` | 加 `memory_context: str` 参数注入 prompt |
| [agents/chat_agent.py](agents/chat_agent.py) `handle_message` | 同上 |
| 新建 `graphs/memory.py` | LangMem store + 检索 helpers + 衰减定时任务 |
| 新建 `agents/memory_extractor.py` | weekly LLM reflection batch |
| 新建 `frontend/src/pages/MemoryPage.tsx` | Memory Inspector UI |

**完成标志**：用两周后，去 LangSmith 看 trace 能见到 memory 检索结果在影响 task_agent 输出；前端 Memory Inspector 能列出 ~10-30 条 memory，用户能编辑/删除；至少一次衰减循环跑过。

### Phase D —— 用户自带 MCP 的扩展接口 (~1 周)

**目标**：开放一个**简洁的扩展点**，让用户能挂自己的 MCP server（笔记 / RAG / 行业知识库 / 代码仓库），主图按需调用它，获取个人语境。

**不做的事**（明确划界）：
- ❌ **不做** MEQ 问卷 onboarding —— 用户烦
- ❌ **不做** role 类目（"surgeon" / "phd_student"）—— 没意义，用户自己的 RAG 比类目准
- ❌ **不做**预定义的结构化 task_lexicon —— 让用户的 MCP 自己回答"这个任务对我意味着什么"
- ❌ **不在 Settings 里做复杂的"职业 profile"页面** —— 只做一个 MCP 列表管理

**核心改动**：

1. **UserPreferences 加一个简单字段**（不是 UserProfile 单独的概念）：
   ```python
   class UserPreferences(BaseModel):
       # ... 现有字段 ...
       chronotype: Literal["lark", "third_bird", "owl"] = "third_bird"
       # ↑ 默认 third_bird（65% 人群），用户可手动改；
       #    Phase C memory 收集睡眠模式后可主动推荐切换
       custom_mcp_servers: list[MCPConfig] = []
       # ↑ 用户的 MCP 配置；前端有个简单管理界面
   ```

   chronotype 不强制问卷，**默认值 + 后续 Phase C 自动推断**就够。

2. **`graphs/user_mcp.py`** —— 注册用户 MCP 的辅助层：
   - 启动时把 `custom_mcp_servers` 注册到 LangGraph
   - 暴露统一接口让主图节点能调用："给我关于 task X 的相关 context"
   - 用 `langchain-mcp-adapters` 包装

3. **`rank_tasks_node` 增强**：在调用 task_agent 之前，**如果**用户有 MCP，先问 MCP "关于这些 task 你有什么 context"，把返回结果塞 prompt。完全 opt-in —— 没配 MCP 的用户走默认逻辑。

4. **`fetch_health_node` 用 chronotype 偏移能量曲线**：
   - lark：峰值早 2h
   - third_bird：默认曲线
   - owl：峰值晚 3h
   一行 math 改动，不需要新的 graph 节点。

5. **前端 Settings 页**：加一个"Extensions" section，列出已配置的 MCP servers，能 add / remove。**就这么简单**。

**完成标志**：
- 用户能在 Settings 加一个 MCP server（比如本地 Notion MCP）
- 生成日程时 LangSmith trace 显示主图调用了用户 MCP 并拿到 context
- chronotype 一个下拉框（无问卷）改完，能量曲线峰值时段偏移

**Phase D 工作量从 2 周缩到 ~1 周**。复杂度大幅下降。

### Phase E —— 研究驱动的健康规则引擎 (持续)

**目标**：把健康-认知关系的研究**显式建模**为可解释规则集，让你的项目不只是"凭直觉"调度。

#### 起步：5 条基于 Daniel Pink《When》的规则

[《When: The Scientific Secrets of Perfect Timing》](https://www.rolandberger.com/en/Insights/Publications/Daniel-Pink-on-how-timing-impacts-productivity.html) 是这块最普及的科普汇总，引用了 100+ 篇论文。从中提炼 5 条直接可实现的规则作为起步：

```python
# 规则 1: Afternoon trough (post-lunch dip)
def afternoon_trough(snapshot, target_hour, citation="Pink 2018; Folkard & Tucker 2003"):
    """13:00-15:00 性能下降，医疗错误/车祸/判断力都达峰"""
    if 13 <= target_hour < 15:
        return RuleApplication(modifier="energy *= 0.75", strength=0.9,
            explanation="afternoon trough — schedule admin/light tasks, avoid deep")

# 规则 2: Analytical tasks 偏好峰值时段
def analytical_peak(task, target_hour, chronotype, citation="Pink 2018; May 1999"):
    """分析型任务最适合主峰时段（lark/third_bird 早晨；owl 傍晚）"""
    peak = 9 if chronotype != "owl" else 17
    if task.task_kind == "analytical" and abs(target_hour - peak) <= 2:
        return RuleApplication(modifier="energy_threshold -= 0.1", strength=0.8,
            explanation="optimal time of day for analytical work")

# 规则 3: Insight tasks 偏好"略疲劳"时段
def insight_off_peak(task, target_hour, chronotype, citation="Pink 2018; Wieth & Zacks 2011"):
    """灵感型任务在大脑略松弛时反而更好（lark 下午晚些；owl 早晨）"""
    off_peak = 16 if chronotype != "owl" else 9
    if task.task_kind == "insight" and abs(target_hour - off_peak) <= 2:
        return RuleApplication(modifier="energy_threshold -= 0.1", strength=0.7,
            explanation="off-peak time enhances creative/insight tasks (Wieth & Zacks 2011)")

# 规则 4: Sleep debt 降低 deep capacity
def low_sleep_reduces_deep_capacity(snapshot, citation="Walker 2017; Lim & Dinges 2010"):
    """睡眠 < 6h 减 30% 工作记忆容量"""
    if snapshot.sleep.duration_hours < 6:
        return RuleApplication(modifier="deep_work_threshold += 0.15", strength=0.9,
            explanation="<6h sleep reduces working memory ~30%")

# 规则 5: Chronotype-shifted energy peak
def chronotype_shift_peak(curve, chronotype, citation="Pink 2018; Roenneberg 2007"):
    """lark 峰值早 2h，owl 峰值晚 3h"""
    if chronotype == "lark":
        return RuleApplication(modifier="shift_peak -2h", strength=1.0,
            explanation="lark chronotype: peak alertness ~2h earlier")
    if chronotype == "owl":
        return RuleApplication(modifier="shift_peak +3h", strength=1.0,
            explanation="owl chronotype: peak alertness ~3h later")
```

**关键产品价值**：Pink 引研究说 "time of day 解释 20% performance variance" —— 这给你的产品提供**有学术依据的差异化叙事**，不只是"AI 排程"，是"基于 chronobiology 研究的排程"。

#### 工程实现

- 新建 `agents/health_rules.py`：每条规则一个 named function，注明引用论文
- 主 graph 加 `apply_health_rules_node` 节点（在 fetch_* 之后，rank_tasks 之前），应用所有命中的规则
- 规则用 `RuleApplication(modifier, strength, explanation, citation)` 统一格式
- 前端 health card 增加"今天 agent 应用了哪些规则、依据什么研究"的可展开列表 —— 透明度

#### 持续扩展

之后每次读到新论文加一条规则。一些待研究的方向：
- **Menstrual cycle × cognitive performance**（黄体期 vs 卵泡期对 deep work 的影响）
- **HRV 7-day baseline deviation** 作为 burnout 早期预警
- **Light exposure** (晨光/夜屏) 对当日 chronotype 表现的修正
- **Caffeine timing** 跟 cognitive load 的相互作用

**完成标志**：health card 能展开看到"今天应用了规则 X, 依据论文 Y, 强度 Z"；至少 5 条规则上线；用户能向第三方解释"决策来源"。

---

## 三、关键文件 / 改造范式

| 文件 / 目录 | 操作 | 所属 Phase |
|---|---|---|
| `requirements.txt` | + langgraph langchain-anthropic langchain-core langmem langchain-openai | A |
| `.env.example` | + LANGSMITH_TRACING / HEALTH_SOURCE / 用户 MCP 配置 | A/B/D |
| `storage.py` | 🆕 抽离持久化 | A |
| `agents/llm.py` | 🆕 ChatAnthropic 集中点 | A |
| `agents/nodes.py` | 🆕 graph 节点包装 | A |
| `graphs/` | 🆕 主图 + 子图 + memory + profile | A/C/D |
| `integrations/health/` | 🆕 多 adapter | B |
| `agents/health_rules.py` | 🆕 规则引擎 | E |
| `models/task.py` | ✏️ Task / Subtask 加 `task_kind: analytical\|insight\|admin` | A |
| `models/user.py` | ✏️ UserPreferences 加 `chronotype`；UserProfile + task_lexicon | D |
| `frontend/src/pages/SettingsPage.tsx` | ✏️ 加职业 + MCP 配置 UI | D |
| `agents/orchestrator.py` | ❌ 删除（功能拆到 storage.py + graphs/） | A |
| `CLAUDE.md` | ✏️ 删 "No LangChain"；新增"三层架构"说明 | A |

---

## 四、验证 / 里程碑

| Phase | 完成的标志（可手动验证） |
|---|---|
| A | `curl /schedule/generate` 输出跟之前 byte-by-byte 一致；LangSmith 显示完整 trace tree |
| B | `HEALTH_SOURCE=oura` （即便 stub）启动不报错；现有 apple 路径行为不变 |
| C | 拒绝一个 block → 第二天 trace 里能看到 memory 检索结果影响了 task_agent |
| D | Settings 页能填"phd_student"；profile_graph 在 trace 里被调用；自定义 MCP 能列出工具 |
| E | health card 能展开看到"今天应用了规则 X, 依据论文 Y" |

---

## 五、关键风险 / 待决策

1. **Phase D 的"用户带 MCP"是否要做权限隔离**？
   - 个人单用户：不用做，简单粗暴最快
   - 未来开源给别人用：必须做沙箱
   - **建议**：先按"个人"做，留接口

2. **Health rules engine 用 declarative 还是 LLM-mediated**？
   - Declarative（写 Python function）：可解释、可测试，但维护贵
   - LLM-mediated（把研究 prompt 给 Claude，让它推理）：灵活，但不可追溯
   - **建议**：混合 —— 主流规则 declarative（5-10 条），新规则先用 LLM 探索，稳定后下沉成 declarative

3. **LangMem 的 embedding 用 OpenAI 还是本地**？
   - OpenAI 最方便，几乎免费
   - voyage（Anthropic 系）：跟 Claude 同生态
   - sentence-transformers：完全本地
   - **建议**：默认 OpenAI，留环境变量切换

4. **每个 Phase 之间是否要停下来打磨 UI**？
   - 否则一直在后端忙，前端没跟上
   - **建议**：每个 phase 内部留 1-2 天做前端

---

## 六、为什么 Phase A 必须先做

不能跳到 Phase B/C/D 直接开始，因为：
- Phase B/C/D 都基于 LangGraph 主图扩展，没主图直接加扩展只会让现有 orchestrator 更乱
- LangMem 跟 LangGraph store 接口是配套的，先 LangGraph 再 LangMem 顺
- LangSmith trace 在跨 phase 调试时无价

---

## 七、Sources

- [LangGraph 1.2 — May 2026 release](https://github.com/langchain-ai/langgraph)
- [LangGraph subgraph composition pattern](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangMem + InMemoryStore guide](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [Building Infinite Memory Agents with LangGraph + LangMem + Postgres](https://medium.com/@alonjamanjeetsinh77/building-infinite-memory-agents-a-master-guide-to-langgraph-langmem-and-postgres-05b3cabd689b)
- [langchain-mcp-adapters — 用户自带 MCP 的接入](https://github.com/langchain-ai/langchain-mcp-adapters)
- 健康-认知研究文献（待 Phase E 时整理）：Walker 2017 / Vyazovskiy 2008 / Killgore 2010 等

---

## 八、不做的事

- ❌ 不一次做完 5 个 Phase —— 每个独立有价值，做完一个停下来用一段时间
- ❌ 不重写 [scheduler_agent.py](agents/scheduler_agent.py) 的纯逻辑（不涉及 LLM）
- ❌ 不把"会议管理 / 调度链接 / Akiflow 风格多源 task 聚合"塞进 Phase A-E —— 那是另外的产品方向，等基础架构稳了再说
- ❌ 不引入 PostgresStore（先 InMemoryStore + JSON 持久化兜底）
- ❌ 不做权限隔离 / 多用户（保持单用户假设；架构 hooks 留好）
