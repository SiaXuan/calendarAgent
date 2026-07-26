# Project Memory: Schedule Agent Swift Sidebar

Exported on 2026-07-15.

This document captures the current working memory for this folder so another session can continue without replaying the whole design conversation. It is descriptive, not a formal spec.

## Project Identity

This folder is a SwiftPM macOS frontend prototype for a scheduling agent. The product direction is a right-edge hover sidebar that acts as a lightweight schedule copilot beside Apple Calendar or other desktop work.

The Swift app is not meant to replace the existing web app. It is a second frontend client for the existing backend project — the FastAPI repo one level up from this `cal_swift_frontend/` folder (the repo root).

Important boundary:

- Do not reimplement backend scheduling, LLM, health scoring, memory, task decomposition, or calendar writeback in this Swift repo.
- Do not modify the old backend unless the user explicitly asks.
- Do not replace or break the React web frontend.
- Swift should only own macOS UI, local view state, API calls, decoding, and interaction wiring.

## Current Architecture

Swift package:

```text
Package.swift
Sources/ScheduleAgentCore
Sources/ScheduleAgentApp
Tests/ScheduleAgentCoreTests
```

Main pieces:

- `ScheduleAgentApp`: macOS SwiftUI/AppKit executable.
- `ScheduleAgentCore`: shared scheduling models, state mapping, hover policy, planning helpers, privacy/memory primitives, and tests.
- `SidebarView`: main sidebar UI.
- `ScheduleAgentApp.swift`: right-edge hover window, pin behavior, floating window setup.
- `DayflowAPIClient`: HTTP/SSE client for the existing FastAPI backend at `http://localhost:8000`.
- `MockAssistantPanelState`: still named "Mock", but currently also acts as the Swift panel state and backend schedule mapper.

The README is older than the current implementation. It still describes more local/mock behavior than the app currently has.

## Backend Contract Used By Swift

The Swift frontend currently expects these backend endpoints:

- `POST /schedule/generate` with `{ "date": "YYYY-MM-DD" }`
- `GET /schedule/{YYYY-MM-DD}`
- `GET /schedule/stream/{YYYY-MM-DD}`
- `GET /health/{YYYY-MM-DD}`
- `POST /health`
- `POST /schedule/{YYYY-MM-DD}/write`
- `POST /schedule/{YYYY-MM-DD}/blocks/write`
- `POST /schedule/{YYYY-MM-DD}/pin`
- `POST /chat/agent`
- `POST /chat/agent/confirm`

Key mapping rules:

- `DayflowSchedule.energy_curve` drives the Energy card.
- `energy_source` is meaningful: `"today"`, `"baseline"`, or `"none"`.
- If `energy_source == "none"`, Swift should not draw a fake energy curve.
- `blocks` with `block_type` of `fixed` or `meal` become calendar anchors.
- `blocks` with `block_type` of `scheduled`, `suggested`, or `instant` become editable/syncable agent tasks.
- `unscheduled` items are not directly syncable because they do not have a start time.
- Single-block write identifies a block by `start`.
- Pin/resize identifies a block by `block_key`, computed as `"{task_id}::{title}"`.
- Backend `task_kind` is used as the visible task badge when present, for example `ANALYTICAL` or `INSIGHT`.
- Backend `cognitive_load` is a fallback badge and energy-type signal. It is not the same thing as user priority.

## UI Direction And Preferences

The intended visual reference is macOS native widgets / Control Center rather than a web dashboard.

Current sidebar behavior:

- Right-edge hot zone opens the sidebar on hover.
- Pin button keeps it open.
- Window is floating, transparent, and shadowless at the container level.
- Individual cards carry the visual weight.
- User prefers the sidebar/background to feel integrated with the desktop, not like a heavy opaque web panel.

Visual rules settled so far:

- Avoid green as a generic "synced" state because it reads as "done" and conflicts with active task state.
- Green is reserved for the currently running task/event.
- Running task/event should not use a `NOW` pill.
- Running state should be shown with green title/time treatment plus a green progress line under the card.
- Synced-to-calendar state should be quiet and neutral, currently a blue line check / subtle blue outline.
- Task type badge should not squeeze the title. It belongs on the second row with timing/session metadata.
- Long task titles may use slightly smaller type and up to two lines.
- Upcoming is the main working timeline. Earlier "Today Queue" language has effectively become scheduled upcoming agent blocks.
- Fixed calendar anchors and meals should remain calmer than agent tasks.

## Timeline And Drag Behavior

The Swift timeline should follow the behavior of the existing web frontend, not invent a separate model.

Expected behavior:

- A task card itself is part of the timeline.
- Dragging should work across the full Upcoming timeline, not only by dropping onto another card.
- Drag position maps from y-coordinate to time over the workday.
- Time snaps to 15-minute increments.
- Drop feedback should show a horizontal line and a time badge.
- Conflicts are resolved by the backend `/pin` reflow logic, not by local Swift collision handling.
- Dragging a synced/fixed item is disabled.

Current Swift implementation:

- Uses `ScheduleTimelineDropMapper`.
- Uses `pinBlock(date:blockKey:start:durationMinutes:)`.
- Workday mapping is currently 8:00 to 22:00.

## Pomodoro / Duration Behavior

The backend exposes:

- `focus_minutes`
- `break_minutes`
- `pomodoro_count`

Swift display:

- One session: `1 x 25m · 25+5m`
- Multiple sessions: `N x 25m · TOTALm`

Plus/minus behavior:

- Local state updates immediately.
- Backend resize is debounced.
- Resize calls `/schedule/{date}/pin` with the same `block_key`, original start, and new duration.

## Health / Energy Memory

Backend now distinguishes energy source:

- `today`: real same-day health/manual input exists.
- `baseline`: persisted baseline/manual sleep exists.
- `none`: no health data.

Important product rule:

- Manual sleep input should remain available; do not lock the user out just because data came from backend.
- The UI should show sleep windows and health inputs in human language, not backend-source implementation labels.
- When no energy data exists, show an empty/call-to-action state rather than a fake curve.

The intended longer-term behavior is that manual sleep input becomes long-lived memory/baseline in the backend. Swift should send edits to backend rather than maintain a separate local source of truth.

## Agent / Proposal Flow

The user expects agent changes to have a review layer.

Current shape:

- Command input sends text to `POST /chat/agent`.
- If backend returns a proposal state, Swift shows a Proposed Changes module.
- User can apply via `POST /chat/agent/confirm`.
- Successful agent actions reload schedule from backend.

Important:

- Do not silently mutate schedule UI after an agent says it "will delete/move" if backend returned a proposal that needs confirmation.
- Keep the web backend's proposal/confirm semantics.

## Document Intake Direction

The "Add Task" entry evolved into "Add Document".

Product intent:

- User can drop in a syllabus, brief, PDF, or similar file.
- Agent performs intent inference and routing:
  - read
  - classify
  - align with real calendar/time constraints
  - route to calendar, weekly plan, or memory

Current status:

- Swift has a mock/front-door document intake UI.
- Backend support is not complete in this repo.
- Keep this as an entry point and mock reaction unless backend endpoints are explicitly added elsewhere.

## Running And Stopping

From this folder:

```bash
swift run ScheduleAgentApp
```

Stop Swift frontend:

```bash
pkill -f ScheduleAgentApp
```

Check Swift frontend:

```bash
pgrep -fl ScheduleAgentApp
```

Check backend:

```bash
lsof -nP -iTCP:8000
```

Run tests/build:

```bash
swift test
swift build
```

Common narrower test:

```bash
swift test --filter MockAssistantPanelStateTests
```

## Current Known Caveats

- The project folder itself may not be a git repository even though it sits under a broader Codex workspace.
- `MockAssistantPanelState` name is misleading because it now also carries backend-derived state.
- README is stale relative to the current backend-integrated Swift app.
- There is no explicit in-app "Refresh backend data" button yet; restarting the frontend is often used during development.
- Frontend process can be running while backend is not. In that case UI may show unavailable/empty backend state.
- Input focus in a floating AppKit window was fragile and required explicit window activation/focus preparation.
- Existing backend path is outside this repo; treat it as read-only unless the user explicitly asks to edit it.

## Recent Design Decisions To Preserve

- Keep the sidebar form factor: right-edge hover, optional pin, compact floating panel.
- Keep Upcoming as the primary work surface.
- Treat scheduled agent blocks as upcoming timeline items, not a separate queue.
- Use backend `task_kind`/`cognitive_load`, not fake priority, for badges.
- Do not use green for synced items.
- Use green only for active/current items.
- Use a progress line for active items instead of a `NOW` pill.
- Use backend reflow for drag conflicts.
- Keep Apple Calendar writeback behind explicit user action.

## Useful Files For Future Work

- `Sources/ScheduleAgentApp/SidebarView.swift`
  Main UI, backend calls, interaction wiring.

- `Sources/ScheduleAgentApp/ScheduleAgentApp.swift`
  Hover sidebar window behavior and AppKit window configuration.

- `Sources/ScheduleAgentApp/DayflowAPIClient.swift`
  Backend client and endpoint contract.

- `Sources/ScheduleAgentCore/MockAssistantPanelState.swift`
  Panel state, schedule mapping, metadata dictionaries, synced block tracking.

- `Sources/ScheduleAgentCore/DayflowScheduleModels.swift`
  Backend schedule model decoding.

- `Sources/ScheduleAgentCore/ScheduleDragTargetResolver.swift`
  Timeline drop mapping.

- `Tests/ScheduleAgentCoreTests/MockAssistantPanelStateTests.swift`
  Most coverage for backend schedule mapping and panel behavior.

- `docs/superpowers/plans/2026-06-26-swift-calendaragent-backend-integration.md`
  Older integration plan. Useful for intent, but some implementation details differ from current files.

