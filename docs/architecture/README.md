# 架构图

对外介绍项目时用的四张图，中英各一套。手写 SVG，可以单独引用、也可以拖进幻灯片。

**先打开 [`index.html`](index.html)**（英文版 [`index.en.html`](index.en.html)）—— 双击即可，四张图带说明串在一页里，支持深色模式。

| 图 | 讲什么 |
|---|---|
| `01-system-overview.svg` | 四层分工：macOS 系统 App、Swift 前端、Python 后端、本地 JSON 存储 |
| `02-schedule-dag.svg` | 每日生成日程的 LangGraph DAG，8 个节点；右栏是执行顺序、缓存、出口与重排 |
| `03-chat-agent.svg` | 对话调整的 ReAct 循环、日程副本、影响面分级、五个终止态 |
| `04-data-flow.svg` | 一份课程大纲怎么变成日历上的一个时间块，直到完成记录与复盘 |

中文版在本目录，英文版在 [`en/`](en/)，文件名一一对应。

## 这个项目能做什么

读你的健康数据评估当天能量曲线，把工作、课程、待办和日历上的事件排成适合你的计划，可以用自然语言更改。


## 事实依据

图里的内容核对自代码，出处集中在：

- `graphs/schedule_graph.py`（图 2 的拓扑）
- `graphs/agent_run.py` + `agents/scratch.py`（图 3 的循环与分级）
- `agents/project_service.py` + `agents/multiday_planner.py`（图 4 的项目线）

## 改图

直接改 SVG 源码，坐标都是手写的绝对值，没有生成脚本。改完验一下：

```bash
python3 -c "import xml.dom.minidom; xml.dom.minidom.parse('01-system-overview.svg')"   # XML 合法性
rsvg-convert -w 1240 01-system-overview.svg -o /tmp/check.png                          # 看实际排版
```

字体、颜色写成元素属性而不是 CSS，所以在浏览器、预览、Keynote、GitHub 里渲染一致。改中文版后记得同步 `en/` 里的对应文件。

## 版本控制

`docs/` 其余部分仍在 `.gitignore` 里（内部文档不公开），只有本目录开了白名单：

```gitignore
docs/*
!docs/architecture/
```

因为根目录的 `README.md` / `README.zh-CN.md` 要内嵌这些图，它们必须能被 clone 到。改完图记得连同 README 一起提交。
