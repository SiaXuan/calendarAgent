# dayflow — 健康感知的 AI 日程助理

> English: [README.md](README.md)

**dayflow** 是一个 macOS 上的、按精力安排日程的助理。它是一条**贴右屏边的悬浮侧栏**——鼠标滑到屏幕右缘就滑出来,像给你这一天开的 Control Center,挨着 Apple 日历用。它按「你什么时候真正有精力」来排任务,而不只是按「哪里有空」。

你用大白话告诉它要做什么(或者把课程大纲 / PDF / 截图丢进去),它估时长、拆任务,再把每块塞进当天精力合适的时段,并绕开你日历上真实的日程。要改就跟它对话;只有你点了,它才写进 Apple 日历 / 提醒。

## 能做什么

- **按精力排。** 每天一条**精力曲线**(来自你的睡眠/健康输入)驱动排程:深度/分析型的活排在精力峰值,杂事塞进午后低谷;还会避开用餐、睡前缓冲和你的工作时段。
- **自然语言加任务。** 输入「刷完算法题,约 2 小时,周五截止」→ Claude 解析时长/截止/认知负荷,把大任务拆成有先后的阶段,绕着 Apple 日历上的事件(经 EventKit 本地读)塞进今天。
- **项目 + 多天规划。** 导入课程大纲、PRD、PDF 或粘贴截图 → 变成一个**项目**,摊成一份到截止日的多天计划。每个项目是**一条有记忆的对话**——「再拆细点」「第三周整体往后挪一周」「作业一做完了」。
- **每日结转。** 项目里没做完的活自动上浮到今天(「继续:X」),直到你勾掉——不会无限重排。
- **对话式调整 + 安全闸门。** 「我累了,把下午排轻点」/「把周报挪到 3 点」→ agent 在副本上改并给出方案;小改动乐观直接生效,大改动弹一张**「Proposed Changes」**卡片等你确认。
- **写入你说了算。** 任务同步到 Apple 日历(单个或全部)、完成勾选都由你决定;后端**永不碰 iCloud**——所有日历/提醒读写由 Swift 客户端经 EventKit 完成。

## 怎么用(典型流程)

1. 起后端,`open ScheduleAgent.app`(配置见下)。
2. 鼠标移到**屏幕右缘**——侧栏滑出。
3. 首次:填昨晚**睡眠** → 精力曲线出现。
4. 在命令框输入一条**任务** → 它被解析、拆解,落到今天时间轴上一个合适的时段、绕开你的日历。
5. 要排更大的计划:打开 **Projects** → 粘贴/拖入大纲 → 复核多天分布 → 写进提醒。
6. **用对话调整** → 需要确认时点一下确认。
7. **同步**到 Apple 日历、把做完的**勾掉**;第二天没做完的自动结转。

> 技术栈:FastAPI + LangGraph(经 LangChain 调 Claude)+ 原生 SwiftUI macOS 客户端。后端是纯逻辑,Swift 客户端经 EventKit 承担全部日历/提醒读写(不碰 iCloud/CalDAV)。实现看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),全局蓝图与进度看 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 需要自己配什么

**唯一必填的是 `ANTHROPIC_API_KEY`(你自己的 Claude API key)。** 装完依赖、给了本地日历/提醒权限之后,基本不用再配别的:

| 配置 | 需要吗 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ 必填 | 拆解任务、聊天调整、导入计划都调 Claude。没有它只剩一个退化的关键词兜底,核心功能用不了。到 <https://console.anthropic.com> 申请 |
| `LLM_REASON_MODEL` / `LLM_FAST_MODEL` 等 | 可选 | 默认写死在 `agents/llm.py`。若你的 key 访问不到默认模型,或想换版本,再覆盖 |
| `CALDAV_*` | ❌ 不用 | 走 Swift 主线时后端碰不到 iCloud——日历经 EventKit 由前端上传、提醒本地读。留空即可 |
| `LANGSMITH_*` | 可选 | 只用于把 trace 发到 LangSmith 调试 |
| OpenAI / embedding | ❌ 不用 | 当前记忆是 pre-embedding(置信度 + namespace 过滤) |

前提:真正能跑主线的是 **macOS**(EventKit + Swift 客户端 + 本地提醒的 AppleScript)。非 Mac 目前没有可用前端——Web UI 已暂时弃用(原因见下方「Web 前端」)。

## 首次配置

```bash
# Python 后端
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# 编辑 .env:只需填 ANTHROPIC_API_KEY;CALDAV_* 是 legacy,Swift 主线留空
```

Swift 原生客户端除了 Swift 工具链(Xcode / command-line tools)之外不需要额外安装,见下方「Swift 前端」。

（Web 前端已暂时弃用,见下方「Web 前端」,无需安装。）

## 日常启动

两个终端,都在项目根目录跑:

```bash
# 终端 1 — 后端(FastAPI,:8000)
.venv/bin/uvicorn main:app --reload

# 终端 2 — Swift 客户端(构建注意事项见下方「Swift 前端」)
cd cal_swift_frontend
./make_app.sh            # SwiftPM 构建 → ScheduleAgent.app + ad-hoc 签名
open ScheduleAgent.app   # 经 LaunchServices 启动,TCC 才会弹权限框
```

**停后端。** 前台的话 `Ctrl+C`;丢了终端就杀占 8000 端口的进程:

```bash
lsof -ti:8000 | xargs kill        # 优雅
lsof -ti:8000 | xargs kill -9     # 不肯退就强杀
```

(`--reload` 是父 + worker 两个进程,通常两个 PID,上面命令一起杀。)停 Swift 客户端用 `pkill -f ScheduleAgentApp`;Web UI 同理杀 5173 端口。

### Swift 前端(原生 macOS 客户端 — 主前端)

侧栏在 `cal_swift_frontend/`,承载当前完整功能。它连的是**同一个** :8000 后端,所以先起后端。

**构建运行——别用 `swift run`。** app 通过 **EventKit** 读写系统日历和提醒,而 macOS(TCC)只把这些权限授给一个真正的、签过名的 `.app`;裸 `swift run` 的二进制会被直接拒、连框都不弹。打成 bundle 再跑:

```bash
cd cal_swift_frontend
./make_app.sh            # SwiftPM 构建 → ScheduleAgent.app + ad-hoc 签名
open ScheduleAgent.app   # 经 LaunchServices 启动,TCC 才会弹权限框

# 改完 Swift:重跑 make_app.sh,再 open 一次
pkill -f ScheduleAgentApp   # 停止
```

`make_app.sh` 会在源码旁生成 `ScheduleAgent.app`;它的 `.build/`(约 270MB 编译产物,不是源码)不进 git。

#### 日历与提醒权限

app 申请日历和提醒的**完全访问**(用途字符串在 `cal_swift_frontend/Info.plist`,bundle id 是 `com.dayflow.scheduleagent`):

- 权限框在 app **首次真正写入**时才弹——第一次**导入 /「写入」提醒**、第一次**生成日程(读日历)**。
- 生成日程只在**已授权**时读本地日历(绝不卡在权限弹窗上),没授权就优雅降级。通过一次导入/写入授权后就一直生效。
- **ad-hoc 签名的坑**:用 `make_app.sh` 重新打包可能重置 TCC 授权(签名不稳定),所以重打包后 macOS 可能再弹一次。正式公证 + 稳定签名能修掉,目前还没做。
- 手动重置授权:`tccutil reset Calendar com.dayflow.scheduleagent` 和 `tccutil reset Reminders com.dayflow.scheduleagent`。

### Web 前端(React/Vite — 暂时弃用)

**已暂时弃用。** Web UI 在浏览器里跑,读不了本地 EventKit,原先靠后端联网读 iCloud 日历(CalDAV)拿你的真实日程。为消除冷启动时 CalDAV 全量拉取的卡顿(实测一次刷新 ~15–45 秒),后端已**停用 CalDAV 读日历**,改成只接受 Swift 客户端经 EventKit 上传的日历。Web 端因此拿不到你的真实日历、排程会和固定事件撞车,故暂时搁置。

代码仍在 `frontend/`,CalDAV 适配器(`integrations/caldav_client.py`)也保留着;将来要恢复 Web 或做移动端时,把那条读取路径接回即可(见 `agents/nodes.py::fetch_calendar_node` 里注释保留的 CalDAV 分支)。

## 跑测试

```bash
.venv/bin/python -m pytest -q --ignore=tests/eval   # 全量(约 1s,不联网/不打 LLM)
.venv/bin/python -m pytest tests/test_schedule_graph.py -v   # 单个文件
```

所有外部调用(Claude、CalDAV、AppleScript 提醒)都被 mock,测试离线跑。`tests/eval/` 里**打真 LLM 的 runner**(`run_eval.py`、`run_decomp_eval.py`)日常用 `--ignore=tests/eval` 排除;其中的 `test_*.py`(如任务拆解的 fallback 冒烟)是纯离线的,跟全量一起跑。

### Eval:冒烟 vs 完整 LLM

行为回归(改了 prompt/工具后模型决策是否还合理)用 `tests/eval/`,分两条:

- **冒烟(smoke)**——强制走确定性 fallback、**不打 LLM**,验证链路/数据流/Pydantic 不崩。快、免费、可进 CI。抓不到 LLM *质量*退化。
- **完整(full)**——真 LLM,测真实决策质量(比如任务拆解有没有过度拆)。需要 `ANTHROPIC_API_KEY`,花几分钱。

```bash
.venv/bin/python -m tests.eval.run_eval                       # 聊天 agent 场景(真 LLM)
.venv/bin/python -m tests.eval.run_decomp_eval --mode smoke   # 任务拆解,离线
.venv/bin/python -m tests.eval.run_decomp_eval --mode full    # 任务拆解,真 LLM
```

结果结构化写进 `tests/eval/*_last_run.json`(时间戳、每场景的状态/步数/标题/耗时),方便跨次 diff 复盘。full 模式的 case 状态:`pass` / `fail` / `xfail`(已知未修的 bad case,仍失败)/ `xpass`(已知 bad case 现在通过了 → 去掉 xfail 标记的信号)/ `error`。已知 bad case(如「一次性杂事被过度拆解」,见 `tests/eval/decomp_scenarios.py`)以 xfail 存档,修复后自动翻成 xpass 提醒。

## 可视化 LangGraph 流程

```bash
.venv/bin/python scripts/visualize_graphs.py           # ASCII 到 stdout
.venv/bin/python scripts/visualize_graphs.py mermaid   # mermaid markdown
.venv/bin/python scripts/visualize_graphs.py png       # 写 docs/*.png
```

## 目录结构

- `main.py` — FastAPI 入口
- `agents/` — 各 agent(`task_agent`、`chat_agent`、`health_agent`、`scheduler_agent` 等)+ `nodes.py` 里的 LangGraph 节点包装
- `graphs/` — LangGraph 状态图(`schedule_graph`、`adjust_graph`、`schedule_stream`、`agent_run`)
- `api/` — FastAPI 路由
- `models/` — Pydantic 模型(Task、Subtask、TimeBlock、DaySchedule…)
- `storage.py` — JSON 落盘的内存 store(健康、任务、日程、项目、完成态…)
- `integrations/caldav_client.py` — iCloud CalDAV 适配器(legacy 兜底;Swift 主线走 EventKit)
- `frontend/` — React/Vite UI(暂时弃用,见「Web 前端」)
- `cal_swift_frontend/` — 原生 SwiftUI macOS 客户端(EventKit,主前端)
- `tests/` — pytest 套件(离线)

## 阶段状态

当前:**Phase 4(进行中)**——纯本地转向:Swift 客户端经 EventKit 承担全部日历/提醒读写,后端是纯逻辑、只返回 `{create,update,delete}` 变更集(不碰 iCloud/CalDAV)。已有项目层 + 多天规划 + 每日结转 + 完成追踪。详细进度与整个大框架(含后续的 Apple Health 多源数据、用户自带 MCP、论文驱动的健康规则引擎)见 [docs/ROADMAP.md](docs/ROADMAP.md);现状/决策/技术债看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 可选:LangSmith trace

在 `.env` 里加,即可免费可视化每次图运行和 Claude 调用:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=dayflow
```

重启 uvicorn,trace 出现在 <https://smith.langchain.com>。
