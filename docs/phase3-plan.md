# Phase 3 完成计划 — Health-Aware AI Scheduling Agent

## Context

项目当前状态与 `project_spec.md` 已经有较大偏离（多数是好的演进），Phase 1 完整，Phase 2 部分完成（用 CalDAV/iCloud 替代了 Google + Todoist），Phase 3 已经开了一个头但没收口。这个计划的目的是**收口 Phase 3**：把已经能算出来的日程真正"送出去"，让用户在日历 App 里看见，并补齐已经写完后端但前端缺口的功能。

---

## 一、现状盘点

### Phase 1 ✅ 完成（且超出 spec）
| spec 要求 | 实际实现 | 备注 |
|---|---|---|
| `core/energy_model.py` | [agents/health_agent.py](agents/health_agent.py) | 改名 + 多语言 health summary（用 Claude Haiku 翻译） |
| `core/scheduler.py` | [agents/scheduler_agent.py](agents/scheduler_agent.py) | 加了 lunch/dinner 自动午餐晚餐插槽 |
| `core/task_decomposer.py` | [agents/task_agent.py](agents/task_agent.py) | 用 Claude Haiku 排序 + Sonnet 拆解 |
| Pydantic 模型 | [models/](models/) | 加了 `phase_label`、`pomodoro_count`、`is_uncertain`、`is_instant`、用户偏好和 i18n |
| `POST /health`、`POST /tasks`、`POST /schedule/generate` | [api/](api/) | 全有，且加了 SSE 流式 `/schedule/stream/{date}` |
| Mock data + 测试 | [tests/](tests/) | `test_health_agent.py`、`test_scheduler_agent.py` |

### Phase 2 ⚠️ 部分完成（路线偏离 spec）
| spec 要求 | 实际实现 |
|---|---|
| Google Calendar OAuth | ❌ 没做（被 CalDAV 取代） |
| Todoist REST | ❌ 没做（被 iOS Reminders 取代） |
| CalDAV (Apple/Outlook) | ✅ [integrations/caldav_client.py](integrations/caldav_client.py) — 真实 HTTP，含 AppleScript 兜底读 Reminders.app |
| `core/calendar_parser.py` | ✅ [agents/calendar_agent.py](agents/calendar_agent.py) |
| Railway 部署 | ⚠️ [railway.toml](railway.toml) 配置在了，部署状态未验证 |
| **额外**：iPhone Shortcuts → `/health/import-simple`、`/tasks/push-reminder` | ✅ |
| **额外**：JSON 文件持久化（[data/](data/)） | ✅ |
| **额外**：React + Vite + Tailwind 前端 + i18n | ✅ |

### Phase 3 🚧 已起步未收口
| spec 要求 | 现状 |
|---|---|
| Chat agent (`POST /chat`) | ✅ 后端通（[api/chat.py](api/chat.py) + [agents/chat_agent.py](agents/chat_agent.py)），❌ 前端 [ChatPage.tsx](frontend/src/pages/ChatPage.tsx) 只是占位符 |
| Calendar write-back | ❌ 完全没做。`UserPreferences.auto_write_to_calendar` 这个开关存在但永远 false，没有 `caldav_client.write_event()` |
| Daily briefing | ❌ 没做 |
| Web UI / Telegram bot | ✅ Web UI 已经替代 Telegram |
| **额外**：per-task 规划聊天（[agents/task_chat_agent.py](agents/task_chat_agent.py)） | ✅ 后端 + 前端 [TaskChatModal](frontend/src/components/TaskChatModal.tsx) 都有 |

---

## 二、Phase 3 收尾路线（按价值排序）

### Step 1 — Calendar 写回 iCloud（最高价值）
**为什么先做这个**：现在整条流水线最后输出一个 JSON `DaySchedule`，但用户日常用的是日历 App。不写回，整套系统对用户来说"看不见"。

**改动点：**

1. [integrations/caldav_client.py](integrations/caldav_client.py) — 新增两个函数：
   - `write_event(title, start, end, tag, description=None) -> uid`
     - 用 `icalendar.Event` 构造 VEVENT
     - 在 `DESCRIPTION` 里嵌 `[agent-scheduled:{tag}]`，方便后续识别和清理（`calendar_agent.classify_event` 已经识别 `[agent-scheduled]` 标记，复用即可）
     - 走现有 `_make_client()` → `principal.calendar()` 路径
     - 复用现有的 `_to_naive_local()` 做时区处理
   - `delete_events_with_tag(date, tag) -> int`
     - 删该日所有 description 含 `[agent-scheduled:{tag}]` 的事件
     - 用于"重生成时先清理上次写入"

2. [agents/orchestrator.py](agents/orchestrator.py) — 新增 `write_schedule_to_calendar(date)`：
   - 读 `schedule_store[date]`
   - 先 `delete_events_with_tag(date, "dayflow")`
   - 对每个 `block_type == scheduled` 或 `block_type == meal` 的 block 调 `write_event()`
   - 返回写入数量

3. [api/schedule.py](api/schedule.py) — 新增 `POST /schedule/{date}/write`，返回 `{written: N}`。
   - 如果 `UserPreferences.auto_write_to_calendar == True`，则 `POST /schedule/generate` 之后自动触发一次写回。

4. [frontend/src/pages/TodayPage.tsx](frontend/src/pages/TodayPage.tsx) — TodayPage 加一个按钮"写入日历"，调 `POST /schedule/{date}/write`，成功后 toast 显示写入数量。

**Gotchas（已经验证过）：**
- iCloud CalDAV 不可靠地支持 RRULE 写入 — 我们只写单次事件，没这个问题
- 时区：写入用本地 naive datetime，读出走的也是 naive，已经一致

### Step 2 — 前端 Chat UI
**为什么排第二**：后端已经能返回调整后的 schedule，但 [ChatPage.tsx](frontend/src/pages/ChatPage.tsx) 是空的，整个 chat 功能用户根本用不到。

**改动点（仅前端）：**

1. [frontend/src/api/chat.ts](frontend/src/api/chat.ts) — 新建 client 函数 `sendChatMessage(date, message) -> DaySchedule`（如果还没有）。
2. [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) — 完整重写：
   - 本地维护 `messages: {role, text}[]` 数组（后端无状态，前端管历史展示即可）
   - 输入框 + 发送按钮
   - 发送后调 `POST /chat`，把 assistant 回复加进消息列表
   - 如果返回了新的 `DaySchedule`，用 TanStack Query 触发 TodayPage 的 `schedule` 缓存失效，并显示 "已更新今日日程"
   - 复用现有的 [TaskChatModal](frontend/src/components/TaskChatModal.tsx) 的消息气泡样式
3. i18n：在 `src/locales/{en,zh-CN,zh-TW,ja}.json` 里加 placeholder/送出按钮等字符串。

### Step 3 — 每日简报 `/briefing/{date}`
**为什么排第三**：所有数据都在 `DaySchedule` 里，纯组装、价值集中。可以做成早晨第一眼看到的入口。

**改动点：**

1. [api/schedule.py](api/schedule.py)（或新建 `api/briefing.py`）— 新增 `GET /briefing/{date}`，返回：
   ```python
   class Briefing(BaseModel):
       headline: str          # "5 tasks today, peak focus 10–12"
       top_priorities: list[str]   # 排前三的 scheduled blocks 的 title
       energy_peak_window: tuple[int, int]   # 来自 health_agent.score_windows
       unscheduled_count: int
       unscheduled_titles: list[str]
       health_summary: str    # 复用 schedule.health_summary
   ```
2. 实现里直接读 `schedule_store[date]`，没有就先调 `orchestrator.generate_day_schedule(date)`。
3. 用户偏好的 `language` 决定 headline/标签的语言；headline 模板可以纯字符串拼接，不用 LLM。
4. [frontend/src/pages/TodayPage.tsx](frontend/src/pages/TodayPage.tsx) 顶部加一张 BriefingCard，调 `GET /briefing/{date}`。

### Step 4（可选）— 自动写回触发
- [api/health.py](api/health.py) 的 `POST /health` 之后，如果 `auto_generate_on_health_sync == True` 就生成；如果 `auto_write_to_calendar == True` 就再写回。把这两个偏好串成一条早晨自动管线："Apple Shortcut 推睡眠 → 自动生成日程 → 自动写到日历"。

---

## 三、关键文件汇总（按改动顺序）

| 文件 | 操作 | 说明 |
|---|---|---|
| [integrations/caldav_client.py](integrations/caldav_client.py) | 新增 `write_event` + `delete_events_with_tag` | Step 1 核心 |
| [agents/orchestrator.py](agents/orchestrator.py) | 新增 `write_schedule_to_calendar` | Step 1 |
| [api/schedule.py](api/schedule.py) | 新增 `POST /schedule/{date}/write` 和 `GET /briefing/{date}` | Step 1 + Step 3 |
| [frontend/src/api/](frontend/src/api/) | 加 `chat.ts`、`briefing.ts`、`schedule.ts` 写回函数 | Step 1/2/3 |
| [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) | 完整重写 | Step 2 |
| [frontend/src/pages/TodayPage.tsx](frontend/src/pages/TodayPage.tsx) | 加"写入日历"按钮 + BriefingCard | Step 1 + Step 3 |
| [frontend/src/locales/](frontend/src/locales/) | 新增 chat/briefing 字符串 | Step 2/3 |
| [tests/](tests/) | 新增 `test_caldav_writeback.py`（用 mock CalDAV）+ briefing 装配测试 | 全部 |

---

## 四、验证方式

**Step 1 — 写回：**
```bash
uvicorn main:app --reload
curl -X POST localhost:8000/schedule/generate -d '{"date":"2026-04-30"}'
curl -X POST localhost:8000/schedule/2026-04-30/write
# 在 macOS 日历 App 里能看到当日的 scheduled blocks
curl -X POST localhost:8000/schedule/2026-04-30/write   # 第二次调用：应该先删旧的再写新的，不重复
```

**Step 2 — 前端 Chat：**
```bash
cd frontend && npm run dev
# 浏览器打开 → Chat tab → 发"我今天累，把下午的任务取消"
# 检查：发出去 → 看到 assistant 回复 → 切回 Today tab → 日程已更新
```

**Step 3 — 简报：**
```bash
curl localhost:8000/briefing/2026-04-30 | jq
# 检查 headline 的语言匹配 GET /preferences 里的 language
# 前端 TodayPage 顶部应显示 BriefingCard
```

**单元测试：**
```bash
pytest tests/ -v
# 新增的 caldav writeback 测试应通过（用 monkeypatch 替换 _make_client）
```

---

## 五、不做的事（明确划界）

- ❌ 不补 Google Calendar OAuth（CalDAV 已经覆盖用户实际需求）
- ❌ 不补 Todoist（iOS Reminders 已经替代）
- ❌ 不做 Telegram bot（前端 Web UI 已经替代）
- ❌ 不引入数据库（JSON 文件足够 Phase 3 范围；如果真的要上 Railway 多用户，再做 PG）
- ❌ 不给 chat 加服务端会话历史（前端管展示就够，加复杂度收益小）

---

## 六、估算

- Step 1 (CalDAV writeback)：~200 行后端 + 30 行前端，半天
- Step 2 (Chat UI)：~150 行前端，半天
- Step 3 (Briefing)：~80 行后端 + 60 行前端，2-3 小时
- Step 4 (自动写回串联)：~30 行，1 小时

总：1.5 个工作日左右，全部 ✅ 之后 Phase 3 的"close the loop"目标就达成了。
