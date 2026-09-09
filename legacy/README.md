# legacy/

放**已经不用、但先不删**的东西。移到这里 = 「主线不再走这条路，代码留着当参考或备将来恢复」。

主线是：Swift 客户端经 EventKit 承担全部日历/提醒读写，后端是纯逻辑、只返回变更集。

## web-frontend/（2026-09-07 从 `frontend/` 移入）

React/Vite 写的 Web UI（Today / Tasks / Chat / Settings）。前端转向原生 SwiftUI 之后就没再维护，`main.py` 也从没挂载过它。

留着的理由：将来要恢复 Web 端或做移动端时，这套页面和 API 调用层可以直接参考。

**注意**：它是几个后端路由**唯一**的调用方 —— `POST /tasks/sync/reminders`、`POST /tasks/reclassify`、`POST /chat/task/{id}`。Swift 客户端不调这些。所以这些路由现在没有活的客户端，但后端仍然可用（也仍被测试覆盖）。

跑它需要 `pnpm install`，路径变了所以 `pnpm-workspace.yaml` 也跟着进来了。

## 还没移进来的

- `integrations/caldav_client.py` —— ROADMAP 线 B Step 5 计划把它移到 `legacy/caldav/`，但**还不能移**：两条活路径在用它。见 `docs/ARCHITECTURE.md` §11。
