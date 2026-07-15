# Schedule Agent

A macOS-first scheduling agent prototype for Apple Calendar workflows.

## What is implemented

- `ScheduleAgentCore`: tested scheduling domain logic.
- `TaskInbox`: add, update, and remove tasks before planning.
- `SchedulingEngine`: proposes task calendar blocks around fixed events, deadlines, buffers, priorities, and personal memory signals.
- `MemoryVault`: lets users view, edit, and delete personal context.
- `PrivacyContextBuilder`: prepares minimized LLM context by withholding calendar event titles and sending busy-block summaries.
- `CalendarWriting`: requires explicit plan confirmation before writing calendar blocks.
- `ScheduleAgentApp`: a SwiftUI floating sidebar prototype with Inbox, Memory, Plan Review, Draft, and Confirm flows.
- `AppleCalendarAdapter`: EventKit source adapter for reading Apple Calendar events and writing confirmed agent-created blocks.

## Run

```bash
swift test
swift build
```

The SwiftPM executable target compiles the SwiftUI/AppKit sidebar. For a production macOS app bundle, the next step is to wrap it in an Xcode project with Calendar usage descriptions, hardened runtime settings, and EventKit entitlements/authorization UX.

## Current boundaries

- The UI uses sample tasks and sample events so the scheduling loop is immediately inspectable.
- The confirm button writes to an in-memory writer in the prototype UI; the EventKit writer is implemented separately and ready to wire after app-bundle permissions are configured.
- The LLM layer is represented by protocols, privacy-safe prompt context, and a local fallback agent. A cloud model client can be added behind `LLMAgentCore` without changing the scheduling engine.
