# legacy/

放**已经不用、但先不删**的东西。移到这里 = 「主线不再走这条路，代码留着当参考或备将来恢复」。

主线是：Swift 客户端经 EventKit 承担全部日历/提醒读写，后端是纯逻辑、只返回变更集。

## web-frontend/（2026-09-07 从 `frontend/` 移入）

React/Vite 写的 Web UI（Today / Tasks / Chat / Settings）。前端转向原生 SwiftUI 之后就没再维护，`main.py` 也从没挂载过它。

留着的理由：将来要恢复 Web 端或做移动端时，这套页面和 API 调用层可以直接参考。

**注意**：它是几个后端路由**唯一**的调用方 —— `POST /tasks/sync/reminders`、`POST /tasks/reclassify`、`POST /chat/task/{id}`。Swift 客户端不调这些。所以这些路由现在没有活的客户端，但后端仍然可用（也仍被测试覆盖）。

跑它需要 `pnpm install`，路径变了所以 `pnpm-workspace.yaml` 也跟着进来了。

## caldav/（2026-10-19 移入）

后端经 iCloud CalDAV 读写日历的整条实现。

- `caldav_client.py` —— 原 `integrations/caldav_client.py`：连接、发现、读写事件、删除。
- `writeback.py` —— 原 `agents/calendar_writeback.py` 的后半截：把一天的日程写回日历、单块写入、删除、完成态提升为历史。前半截（tag 与 block_key 的生成）是纯函数、仍在用，留在原处。
- `test_writeback_diff.py` —— 对应的测试。`pytest.ini` 的 `norecursedirs` 把 `legacy/` 排除在收集之外。

**为什么退役**：Swift 客户端经 EventKit 接管了全部日历读写，后端只返回变更集。除了架构原因，这条路还有两个实际问题——它**够不着**现在的事件（事件写在本地 EventKit 日历里，CalDAV 看不见），而且慢：对真实账号实测一次调用 ~20 秒，哪怕 tag 一条都没匹配上，因为它要把整个日历拉下来搜。表现出来就是「删除报失败、刷新又好了」，以及每次勾完成都卡二十秒。

**提醒读取不在这里**：`_fetch_reminders_applescript` 原本也住在 `caldav_client.py` 里，但它跟 CalDAV 毫无关系——它读的是本地 Reminders.app。已经拆到 `integrations/reminders_applescript.py`，作为没有客户端时的降级路径保留。这个错位的归档是「CalDAV 阴魂不散」的主要来源：慢请求的调用栈里一直出现 caldav_client，而那几十秒其实是 AppleScript。

`requirements.txt` 里的 `caldav` / `icalendar` 现在只为这个目录保留。
