# Swift CalendarAgent Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the Swift macOS sidebar frontend to the existing `/Users/siaxuan/Desktop/calendarAgent` FastAPI backend while keeping the React web frontend intact.

**Architecture:** Treat the Swift app as a second client of the existing FastAPI server at `http://localhost:8000`. Add a small Swift HTTP client, DTO layer, mapper, and `ObservableObject` view model in this Swift project; do not move, fork, rewrite, or replace the Python/React project. Keep mock data only as a fallback when the existing backend is not running.

**Tech Stack:** SwiftPM, SwiftUI, URLSession async/await, Swift Testing, existing FastAPI endpoints from `Desktop/calendarAgent`.

---

## Hard Boundary: Reuse The Existing Backend

This plan is **not** a plan to build a new backend.

Implementation must reuse `/Users/siaxuan/Desktop/calendarAgent` as the source of truth:

- Do not create new Python routes in this Swift repo.
- Do not reimplement scheduling, health scoring, writeback, task decomposition, memory, or chat logic in Swift.
- Do not copy backend business logic from `Desktop/calendarAgent` into this repo.
- Do not replace the React web frontend; it should keep working against the same FastAPI server.
- Swift should only add client-side networking, decoding, state mapping, and UI action wiring.
- If an endpoint is missing, record the gap and keep the corresponding Swift feature mocked or disabled instead of inventing a parallel backend.

---

## Backend Contract To Preserve

The old project already exposes the data needed by the Swift sidebar:

- `GET /schedule/{YYYY-MM-DD}` returns `DaySchedule`.
- `POST /schedule/generate` with `{ "date": "YYYY-MM-DD" }` generates and caches a schedule.
- `GET /schedule/stream/{YYYY-MM-DD}` streams schedule generation; defer SSE for the first Swift integration.
- `POST /schedule/{YYYY-MM-DD}/write` writes the cached schedule to iCloud Calendar through the old backend.
- `POST /schedule/{YYYY-MM-DD}/blocks/write` writes one cached block. It identifies the block by `start`, not by task id.
- `POST /schedule/{YYYY-MM-DD}/pin` moves/resizes a scheduled block. It identifies the block by `block_key`, which is `"{task_id}::{title}"`.
- `GET /health/{YYYY-MM-DD}` returns external/manual health data.
- `GET /tasks` returns raw tasks/reminders. Use it only as a fallback; the sidebar's primary queue should come from schedule blocks.
- `POST /chat/agent` handles conversational schedule changes.

Important product mapping:

- Swift `Energy` card reads `DaySchedule.energy_curve`; health detail reads `/health/{date}` when available.
- Swift `Today Queue` should primarily show scheduled-but-not-yet-synced `DaySchedule.blocks` where `block_type` is `scheduled`, `suggested`, or `instant`. These have `start` and can be synced one-by-one.
- `DaySchedule.unscheduled` should be shown as a risk/overflow section, not as the main syncable queue, because unscheduled tasks have no `start` and cannot be passed to `/blocks/write`.
- Swift `Upcoming` should show fixed calendar blocks plus already scheduled/agent blocks sorted by time. After a successful sync, reload the schedule and visually treat the synced block as calendar-backed.
- Swift drag-to-adjust must preserve `block_key`; without it the `/pin` endpoint cannot locate the block.
- The backend writes through CalDAV/iCloud, not native macOS EventKit. This is acceptable for the first integration.

---

## File Structure

- Create `Sources/ScheduleAgentCore/CalendarAgentBackendDTOs.swift`  
  Decodable DTOs matching the FastAPI/React types.

- Create `Sources/ScheduleAgentCore/BackendPanelMapper.swift`  
  Converts backend DTOs into the sidebar state and stores backend references needed for write/pin actions.

- Modify `Sources/ScheduleAgentCore/MockAssistantPanelState.swift`  
  Add backend metadata dictionaries and helper methods for replacing state from backend data. Keep mock methods as fallback.

- Create `Sources/ScheduleAgentApp/CalendarAgentBackendClient.swift`  
  URLSession client for the existing FastAPI endpoints.

- Create `Sources/ScheduleAgentApp/AssistantPanelViewModel.swift`  
  `@MainActor ObservableObject` that owns loading, fallback, sync, pin, and chat actions.

- Modify `Sources/ScheduleAgentApp/SidebarView.swift`  
  Replace direct `@State private var state = MockAssistantPanelState.sample()` with `@StateObject private var viewModel = AssistantPanelViewModel()`. Wire button actions to view model async methods.

- Create `Tests/ScheduleAgentCoreTests/BackendPanelMapperTests.swift`  
  Tests mapping of schedule blocks, energy curve, backend references, and unscheduled overflow.

- Create `Tests/ScheduleAgentCoreTests/CalendarAgentBackendDTOTests.swift`  
  Tests decoding sample JSON copied from the old React `types.ts` contract.

---

### Task 1: Add Backend DTOs

**Files:**
- Create: `Sources/ScheduleAgentCore/CalendarAgentBackendDTOs.swift`
- Test: `Tests/ScheduleAgentCoreTests/CalendarAgentBackendDTOTests.swift`

- [ ] **Step 1: Write DTO decoding tests**

Add `Tests/ScheduleAgentCoreTests/CalendarAgentBackendDTOTests.swift`:

```swift
import Foundation
import Testing
@testable import ScheduleAgentCore

struct CalendarAgentBackendDTOTests {
    @Test
    func decodesDayScheduleFromBackendJSON() throws {
        let json = """
        {
          "date": "2026-06-26",
          "energy_curve": [0.3, 0.8, 0.4],
          "health_summary": "Health data loaded",
          "blocks": [
            {
              "start": "2026-06-26T09:00:00",
              "end": "2026-06-26T09:45:00",
              "block_type": "scheduled",
              "task_id": "task-1",
              "title": "Write proposal",
              "cognitive_load": "deep",
              "task_kind": "analytical",
              "notes": "Best in morning",
              "phase_label": "draft",
              "focus_minutes": 45,
              "break_minutes": 5,
              "pomodoro_count": 1,
              "deadline": "2026-06-27",
              "is_uncertain": false,
              "has_explicit_time": true
            }
          ],
          "unscheduled": [
            {
              "parent_id": "task-2",
              "title": "Read paper",
              "cognitive_load": "medium",
              "estimated_minutes": 60,
              "phase_label": null,
              "deadline": "2026-06-27"
            }
          ]
        }
        """.data(using: .utf8)!

        let schedule = try JSONDecoder.calendarAgent.decode(BackendDaySchedule.self, from: json)

        #expect(schedule.date == "2026-06-26")
        #expect(schedule.energyCurve == [0.3, 0.8, 0.4])
        #expect(schedule.blocks.first?.blockType == .scheduled)
        #expect(schedule.blocks.first?.blockKey == "task-1::Write proposal")
        #expect(schedule.unscheduled.first?.estimatedMinutes == 60)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
swift test --filter CalendarAgentBackendDTOTests
```

Expected: fails because `BackendDaySchedule` and `JSONDecoder.calendarAgent` do not exist.

- [ ] **Step 3: Implement DTOs**

Add `Sources/ScheduleAgentCore/CalendarAgentBackendDTOs.swift`:

```swift
import Foundation

public extension JSONDecoder {
    static var calendarAgent: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

public extension JSONEncoder {
    static var calendarAgent: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}

public enum BackendBlockType: String, Codable, Sendable {
    case fixed
    case meal
    case suggested
    case scheduled
    case instant
}

public enum BackendCognitiveLoad: String, Codable, Sendable {
    case light
    case medium
    case deep
}

public enum BackendTaskKind: String, Codable, Sendable {
    case analytical
    case insight
    case admin
}

public struct BackendScheduleBlock: Codable, Equatable, Sendable {
    public var start: String
    public var end: String
    public var blockType: BackendBlockType
    public var taskID: String?
    public var title: String
    public var cognitiveLoad: BackendCognitiveLoad?
    public var taskKind: BackendTaskKind?
    public var notes: String?
    public var phaseLabel: String?
    public var focusMinutes: Int
    public var breakMinutes: Int
    public var pomodoroCount: Int
    public var deadline: String?
    public var isUncertain: Bool
    public var hasExplicitTime: Bool

    public var blockKey: String? {
        guard let taskID else { return nil }
        return "\(taskID)::\(title)"
    }

    enum CodingKeys: String, CodingKey {
        case start
        case end
        case blockType = "block_type"
        case taskID = "task_id"
        case title
        case cognitiveLoad = "cognitive_load"
        case taskKind = "task_kind"
        case notes
        case phaseLabel = "phase_label"
        case focusMinutes = "focus_minutes"
        case breakMinutes = "break_minutes"
        case pomodoroCount = "pomodoro_count"
        case deadline
        case isUncertain = "is_uncertain"
        case hasExplicitTime = "has_explicit_time"
    }
}

public struct BackendUnscheduledTask: Codable, Equatable, Sendable {
    public var parentID: String
    public var title: String
    public var cognitiveLoad: BackendCognitiveLoad?
    public var estimatedMinutes: Int
    public var phaseLabel: String?
    public var deadline: String?

    enum CodingKeys: String, CodingKey {
        case parentID = "parent_id"
        case title
        case cognitiveLoad = "cognitive_load"
        case estimatedMinutes = "estimated_minutes"
        case phaseLabel = "phase_label"
        case deadline
    }
}

public struct BackendDaySchedule: Codable, Equatable, Sendable {
    public var date: String
    public var energyCurve: [Double]
    public var blocks: [BackendScheduleBlock]
    public var unscheduled: [BackendUnscheduledTask]
    public var healthSummary: String

    enum CodingKeys: String, CodingKey {
        case date
        case energyCurve = "energy_curve"
        case blocks
        case unscheduled
        case healthSummary = "health_summary"
    }
}

public struct BackendHealthSnapshot: Codable, Equatable, Sendable {
    public struct Sleep: Codable, Equatable, Sendable {
        public var durationHours: Double
        public var sleepStart: String
        public var sleepEnd: String

        enum CodingKeys: String, CodingKey {
            case durationHours = "duration_hours"
            case sleepStart = "sleep_start"
            case sleepEnd = "sleep_end"
        }
    }

    public var date: String
    public var sleep: Sleep
    public var restingHeartRate: Int?
    public var hrv: Double?
    public var steps: Int?
    public var activeMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case date
        case sleep
        case restingHeartRate = "resting_heart_rate"
        case hrv
        case steps
        case activeMinutes = "active_minutes"
    }
}

public struct BackendWriteResponse: Codable, Equatable, Sendable {
    public var written: Int
    public var deleted: Int?
    public var skipped: Bool?
}

public struct BackendPinResponse: Codable, Equatable, Sendable {
    public var blockKey: String
    public var start: String
    public var durationMinutes: Int
    public var adjusted: Bool
    public var schedule: BackendDaySchedule

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case start
        case durationMinutes = "duration_min"
        case adjusted
        case schedule
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
swift test --filter CalendarAgentBackendDTOTests
```

Expected: PASS.

---

### Task 2: Map Backend Schedule Into Sidebar State

**Files:**
- Create: `Sources/ScheduleAgentCore/BackendPanelMapper.swift`
- Modify: `Sources/ScheduleAgentCore/MockAssistantPanelState.swift`
- Test: `Tests/ScheduleAgentCoreTests/BackendPanelMapperTests.swift`

- [ ] **Step 1: Write mapper tests**

Add `Tests/ScheduleAgentCoreTests/BackendPanelMapperTests.swift`:

```swift
import Foundation
import Testing
@testable import ScheduleAgentCore

struct BackendPanelMapperTests {
    @Test
    func mapsScheduledBlocksToQueueAndUpcomingWithBackendReferences() throws {
        let schedule = BackendDaySchedule(
            date: "2026-06-26",
            energyCurve: [0.2, 0.9, 0.3],
            blocks: [
                BackendScheduleBlock(
                    start: "2026-06-26T09:00:00",
                    end: "2026-06-26T09:45:00",
                    blockType: .scheduled,
                    taskID: "task-1",
                    title: "Write proposal",
                    cognitiveLoad: .deep,
                    taskKind: .analytical,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 45,
                    breakMinutes: 5,
                    pomodoroCount: 1,
                    deadline: "2026-06-27",
                    isUncertain: false,
                    hasExplicitTime: true
                ),
                BackendScheduleBlock(
                    start: "2026-06-26T11:00:00",
                    end: "2026-06-26T11:30:00",
                    blockType: .fixed,
                    taskID: nil,
                    title: "Product review",
                    cognitiveLoad: nil,
                    taskKind: nil,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 30,
                    breakMinutes: 0,
                    pomodoroCount: 1,
                    deadline: nil,
                    isUncertain: false,
                    hasExplicitTime: true
                )
            ],
            unscheduled: [
                BackendUnscheduledTask(
                    parentID: "task-2",
                    title: "Read paper",
                    cognitiveLoad: .medium,
                    estimatedMinutes: 60,
                    phaseLabel: nil,
                    deadline: "2026-06-27"
                )
            ],
            healthSummary: "Health data loaded"
        )

        let state = BackendPanelMapper.map(schedule: schedule, health: nil, previous: .sample())

        #expect(state.energyCurve == [0.2, 0.9, 0.3])
        #expect(state.todayQueue.map(\\.title) == ["Write proposal"])
        #expect(state.upcomingEvents.map(\\.title).contains("Product review"))

        let queueID = try #require(state.todayQueue.first?.id)
        #expect(state.backendTaskIDs[queueID] == "task-1")

        let scheduledEvent = try #require(state.upcomingEvents.first { $0.title == "Write proposal" })
        #expect(state.backendBlockReferences[scheduledEvent.id]?.startISO == "2026-06-26T09:00:00")
        #expect(state.backendBlockReferences[scheduledEvent.id]?.blockKey == "task-1::Write proposal")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
swift test --filter BackendPanelMapperTests
```

Expected: fails because `BackendPanelMapper`, `backendTaskIDs`, and `backendBlockReferences` do not exist.

- [ ] **Step 3: Add backend references to panel state**

Modify `Sources/ScheduleAgentCore/MockAssistantPanelState.swift`:

```swift
public struct BackendBlockReference: Equatable, Sendable {
    public var startISO: String
    public var blockKey: String?
    public var blockType: BackendBlockType
    public var isSynced: Bool

    public init(startISO: String, blockKey: String?, blockType: BackendBlockType, isSynced: Bool = false) {
        self.startISO = startISO
        self.blockKey = blockKey
        self.blockType = blockType
        self.isSynced = isSynced
    }
}
```

Add these properties to `MockAssistantPanelState`:

```swift
public var backendTaskIDs: [UUID: String]
public var backendBlockReferences: [UUID: BackendBlockReference]
public var unscheduledOverflow: [TaskItem]
```

Update its initializer and `sample()` with:

```swift
backendTaskIDs: [:],
backendBlockReferences: [:],
unscheduledOverflow: [],
```

- [ ] **Step 4: Implement mapper**

Add `Sources/ScheduleAgentCore/BackendPanelMapper.swift`:

```swift
import Foundation

public enum BackendPanelMapper {
    public static func map(
        schedule: BackendDaySchedule,
        health: BackendHealthSnapshot?,
        previous: MockAssistantPanelState,
        now: Date = .now,
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) -> MockAssistantPanelState {
        var state = previous
        state.energyCurve = schedule.energyCurve.isEmpty ? previous.energyCurve : schedule.energyCurve
        state.healthSignal = mapHealth(health: health, summary: schedule.healthSummary)
        state.todayQueue = []
        state.upcomingEvents = []
        state.backendTaskIDs = [:]
        state.backendBlockReferences = [:]
        state.unscheduledOverflow = schedule.unscheduled.map { mapUnscheduled($0, calendar: calendar) }

        for block in schedule.blocks {
            guard let start = parseDate(block.start),
                  let end = parseDate(block.end)
            else {
                continue
            }

            let eventID = UUID()
            let event = CalendarEvent(
                id: eventID,
                title: block.title,
                start: start,
                end: end,
                isMovable: block.blockType == .scheduled || block.blockType == .suggested || block.blockType == .instant,
                source: block.blockType == .fixed ? .appleCalendar : .agent
            )
            state.upcomingEvents.append(event)
            state.backendBlockReferences[eventID] = BackendBlockReference(
                startISO: block.start,
                blockKey: block.blockKey,
                blockType: block.blockType,
                isSynced: false
            )

            if block.blockType == .scheduled || block.blockType == .suggested || block.blockType == .instant {
                let taskID = UUID()
                let task = TaskItem(
                    id: taskID,
                    title: block.title,
                    estimatedMinutes: max(5, Int(end.timeIntervalSince(start) / 60)),
                    deadline: block.deadline.flatMap { parseDeadline($0, calendar: calendar) },
                    priority: priority(from: block.cognitiveLoad),
                    project: block.phaseLabel,
                    isSplittable: false,
                    energy: energy(from: block.cognitiveLoad)
                )
                state.todayQueue.append(task)
                if let backendTaskID = block.taskID {
                    state.backendTaskIDs[taskID] = backendTaskID
                }
            }
        }

        state.upcomingEvents.sort { $0.start < $1.start }
        state.nowStatus = status(from: state.upcomingEvents, now: now)
        state.nextEventSummary = nextSummary(from: state.upcomingEvents, now: now)
        state.statusMessage = "Connected to calendarAgent backend."
        return state
    }

    private static func mapHealth(health: BackendHealthSnapshot?, summary: String) -> MockHealthSignal {
        guard let health else {
            return MockHealthSignal(
                hasExternalData: !summary.isEmpty,
                sleepWindow: "No sleep data",
                summary: summary.isEmpty ? "No health data loaded." : summary
            )
        }
        return MockHealthSignal(
            hasExternalData: true,
            sleepWindow: "\(timeOnly(health.sleep.sleepStart))-\(timeOnly(health.sleep.sleepEnd))",
            restingHeartRate: health.restingHeartRate,
            hrv: health.hrv.map(Int.init),
            steps: health.steps,
            summary: summary
        )
    }

    private static func mapUnscheduled(_ task: BackendUnscheduledTask, calendar: Calendar) -> TaskItem {
        TaskItem(
            title: task.title,
            estimatedMinutes: task.estimatedMinutes,
            deadline: task.deadline.flatMap { parseDeadline($0, calendar: calendar) },
            priority: priority(from: task.cognitiveLoad),
            project: task.phaseLabel,
            isSplittable: false,
            energy: energy(from: task.cognitiveLoad)
        )
    }

    private static func priority(from load: BackendCognitiveLoad?) -> TaskPriority {
        switch load {
        case .deep: .high
        case .medium: .medium
        case .light: .low
        case nil: .medium
        }
    }

    private static func energy(from load: BackendCognitiveLoad?) -> EnergyType {
        switch load {
        case .deep: .deepWork
        case .medium: .shallowWork
        case .light: .admin
        case nil: .shallowWork
        }
    }

    private static func status(from events: [CalendarEvent], now: Date) -> String {
        if let current = events.first(where: { $0.start <= now && $0.end > now }) {
            return "In \(current.title)"
        }
        if let next = events.first(where: { $0.start > now }) {
            return "Free until \(next.start.formatted(date: .omitted, time: .shortened))"
        }
        return "Free for the rest of today"
    }

    private static func nextSummary(from events: [CalendarEvent], now: Date) -> String {
        if let current = events.first(where: { $0.start <= now && $0.end > now }) {
            return "Until \(current.end.formatted(date: .omitted, time: .shortened))"
        }
        if let next = events.first(where: { $0.start > now }) {
            return "Next: \(next.title) at \(next.start.formatted(date: .omitted, time: .shortened))"
        }
        return "No more calendar blocks"
    }

    private static func parseDate(_ value: String) -> Date? {
        if let date = ISO8601DateFormatter().date(from: value) {
            return date
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter.date(from: value)
    }

    private static func parseDeadline(_ value: String, calendar: Calendar) -> Date? {
        let parts = value.split(separator: "-").compactMap(Int.init)
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    private static func timeOnly(_ value: String) -> String {
        guard let date = parseDate(value) else { return value }
        return date.formatted(date: .omitted, time: .shortened)
    }
}
```

- [ ] **Step 5: Run mapper tests**

Run:

```bash
swift test --filter BackendPanelMapperTests
```

Expected: PASS.

---

### Task 3: Add URLSession Backend Client

**Files:**
- Create: `Sources/ScheduleAgentApp/CalendarAgentBackendClient.swift`

- [ ] **Step 1: Implement client protocol and URLSession implementation**

Add `Sources/ScheduleAgentApp/CalendarAgentBackendClient.swift`:

```swift
import Foundation
import ScheduleAgentCore

protocol CalendarAgentBackendServing: Sendable {
    func fetchSchedule(date: String) async throws -> BackendDaySchedule
    func generateSchedule(date: String) async throws -> BackendDaySchedule
    func fetchHealth(date: String) async throws -> BackendHealthSnapshot
    func writeSchedule(date: String) async throws -> BackendWriteResponse
    func writeBlock(date: String, startISO: String) async throws -> BackendWriteResponse
    func pinBlock(date: String, blockKey: String, startISO: String?, durationMinutes: Int?) async throws -> BackendPinResponse
}

struct CalendarAgentBackendClient: CalendarAgentBackendServing {
    var baseURL: URL = URL(string: "http://localhost:8000")!
    var session: URLSession = .shared

    func fetchSchedule(date: String) async throws -> BackendDaySchedule {
        try await get("/schedule/\(date)")
    }

    func generateSchedule(date: String) async throws -> BackendDaySchedule {
        try await post("/schedule/generate", body: ["date": date])
    }

    func fetchHealth(date: String) async throws -> BackendHealthSnapshot {
        try await get("/health/\(date)")
    }

    func writeSchedule(date: String) async throws -> BackendWriteResponse {
        try await post("/schedule/\(date)/write", body: EmptyBody())
    }

    func writeBlock(date: String, startISO: String) async throws -> BackendWriteResponse {
        try await post("/schedule/\(date)/blocks/write", body: ["start": startISO])
    }

    func pinBlock(date: String, blockKey: String, startISO: String?, durationMinutes: Int?) async throws -> BackendPinResponse {
        try await post(
            "/schedule/\(date)/pin",
            body: PinBody(blockKey: blockKey, startISO: startISO, durationMinutes: durationMinutes)
        )
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "GET"
        return try await send(request)
    }

    private func post<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder.calendarAgent.encode(body)
        return try await send(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode)
        else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder.calendarAgent.decode(T.self, from: data)
    }

    private struct EmptyBody: Encodable {}

    private struct PinBody: Encodable {
        var blockKey: String
        var startISO: String?
        var durationMinutes: Int?

        enum CodingKeys: String, CodingKey {
            case blockKey = "block_key"
            case startISO = "start_iso"
            case durationMinutes = "duration_min"
        }
    }
}
```

- [ ] **Step 2: Run build**

Run:

```bash
swift build
```

Expected: PASS.

---

### Task 4: Add View Model With Mock Fallback

**Files:**
- Create: `Sources/ScheduleAgentApp/AssistantPanelViewModel.swift`

- [ ] **Step 1: Implement view model**

Add `Sources/ScheduleAgentApp/AssistantPanelViewModel.swift`:

```swift
import Foundation
import ScheduleAgentCore

@MainActor
final class AssistantPanelViewModel: ObservableObject {
    @Published private(set) var state: MockAssistantPanelState
    @Published private(set) var isConnectedToBackend = false
    @Published private(set) var isLoading = false

    private let client: CalendarAgentBackendServing
    private let calendar: Calendar

    init(
        client: CalendarAgentBackendServing = CalendarAgentBackendClient(),
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) {
        self.client = client
        self.calendar = calendar
        self.state = .sample(calendar: calendar)
    }

    func loadToday() async {
        isLoading = true
        defer { isLoading = false }

        let date = todayString()
        do {
            let schedule: BackendDaySchedule
            do {
                schedule = try await client.fetchSchedule(date: date)
            } catch {
                schedule = try await client.generateSchedule(date: date)
            }

            let health = try? await client.fetchHealth(date: date)
            state = BackendPanelMapper.map(schedule: schedule, health: health, previous: state, calendar: calendar)
            isConnectedToBackend = true
        } catch {
            isConnectedToBackend = false
            state.statusMessage = "Backend unavailable. Showing local mock data."
        }
    }

    func syncAll() async {
        let date = todayString()
        do {
            _ = try await client.writeSchedule(date: date)
            await loadToday()
            state.statusMessage = "Synced today's schedule through calendarAgent backend."
        } catch {
            state.statusMessage = "Sync failed. Check that the backend is running on localhost:8000."
        }
    }

    func syncTask(_ task: TaskItem) async {
        let matchingEvent = state.upcomingEvents.first { event in
            event.title == task.title && state.backendBlockReferences[event.id]?.startISO != nil
        }
        guard let matchingEvent,
              let startISO = state.backendBlockReferences[matchingEvent.id]?.startISO
        else {
            state.statusMessage = "This task has no scheduled block yet. Generate a schedule first."
            return
        }

        do {
            _ = try await client.writeBlock(date: todayString(), startISO: startISO)
            await loadToday()
            state.statusMessage = "Synced \(task.title)."
        } catch {
            state.statusMessage = "Single block sync failed."
        }
    }

    func pinEvent(_ event: CalendarEvent, to start: Date? = nil, durationMinutes: Int? = nil) async {
        guard let reference = state.backendBlockReferences[event.id],
              let blockKey = reference.blockKey
        else {
            state.statusMessage = "This calendar block cannot be moved by the backend."
            return
        }

        do {
            let response = try await client.pinBlock(
                date: todayString(),
                blockKey: blockKey,
                startISO: start.map(isoString),
                durationMinutes: durationMinutes
            )
            state = BackendPanelMapper.map(schedule: response.schedule, health: nil, previous: state, calendar: calendar)
            state.statusMessage = response.adjusted ? "Moved to nearest available slot." : "Moved block."
        } catch {
            state.statusMessage = "Move failed."
        }
    }

    func parseCommand(_ input: String) {
        state.parseCommand(input)
    }

    func addParsedTaskToQueue() {
        state.addParsedTaskToQueue()
    }

    private func todayString() -> String {
        let components = calendar.dateComponents([.year, .month, .day], from: Date())
        let year = components.year ?? 1970
        let month = components.month ?? 1
        let day = components.day ?? 1
        return String(format: "%04d-%02d-%02d", year, month, day)
    }

    private func isoString(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
}
```

- [ ] **Step 2: Run build**

Run:

```bash
swift build
```

Expected: PASS.

---

### Task 5: Wire Sidebar UI To View Model

**Files:**
- Modify: `Sources/ScheduleAgentApp/SidebarView.swift`

- [ ] **Step 1: Replace direct state ownership**

In `SidebarView`, replace:

```swift
@State private var state = MockAssistantPanelState.sample()
```

with:

```swift
@StateObject private var viewModel = AssistantPanelViewModel()
private var state: MockAssistantPanelState { viewModel.state }
```

- [ ] **Step 2: Load backend on appear**

Extend the existing `.onAppear` block:

```swift
.onAppear {
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
        commandFocused = true
    }
    Task {
        await viewModel.loadToday()
    }
}
```

- [ ] **Step 3: Wire Today Queue main button**

Replace:

```swift
state.syncQueueToCalendar()
```

with:

```swift
Task {
    await viewModel.syncAll()
}
```

- [ ] **Step 4: Wire single-task secondary button**

Replace:

```swift
state.acceptSingleTask(taskID: task.id)
```

with:

```swift
Task {
    await viewModel.syncTask(task)
}
```

- [ ] **Step 5: Keep mock-only interactions local**

For command parsing and document intake, keep local behavior until the old backend has document intake and natural-language task creation endpoints:

```swift
viewModel.parseCommand(commandText)
viewModel.addParsedTaskToQueue()
```

Do not call `/tasks` for free-form command text in this pass, because `POST /tasks` expects structured fields and is not a natural-language parser.

- [ ] **Step 6: Run build**

Run:

```bash
swift build
```

Expected: PASS.

---

### Task 6: Preserve Drag-To-Adjust Semantics

**Files:**
- Modify: `Sources/ScheduleAgentApp/SidebarView.swift`
- Modify: `Sources/ScheduleAgentApp/AssistantPanelViewModel.swift`

- [ ] **Step 1: Identify current Swift drag surface**

The current Swift draft timeline uses `.draggable(block.taskID.uuidString)` and `state.moveDraftBlock(...)`. Backend pinning needs a `CalendarEvent` or backend block reference, not only a mock task id.

- [ ] **Step 2: Add event pin action to existing drop targets**

Where the UI has a backend-backed event row or future draggable schedule row, call:

```swift
Task {
    await viewModel.pinEvent(event, to: dateAtHour(hour))
}
```

- [ ] **Step 3: Keep mock draft drag behavior for local Plan Draft**

Do not remove:

```swift
state.moveDraftBlock(taskID: taskID, toStart: dateAtHour(hour))
```

until the Plan Draft UI is replaced by backend-generated blocks. The mock draft is still useful when the backend is offline.

- [ ] **Step 4: Run mapper and state tests**

Run:

```bash
swift test --filter BackendPanelMapperTests
swift test --filter MockAssistantPanelStateTests
```

Expected: both PASS.

---

### Task 7: Add Backend Status And Fallback UX

**Files:**
- Modify: `Sources/ScheduleAgentApp/SidebarView.swift`

- [ ] **Step 1: Show backend status in the footer**

Update `statusBar` to include a subtle state:

```swift
let statusText = viewModel.isConnectedToBackend
    ? "Connected: localhost:8000"
    : state.statusMessage
```

Render `statusText` in the existing footer instead of hard-coded mock language.

- [ ] **Step 2: Keep UI usable when backend is down**

Make sure these still work with mock state:

```swift
viewModel.parseCommand(commandText)
viewModel.addParsedTaskToQueue()
state.schedule(taskID: task.id)
state.confirmDraft()
state.dismissDraft()
```

Only backend-dependent actions should show an error status.

- [ ] **Step 3: Run build**

Run:

```bash
swift build
```

Expected: PASS.

---

## Manual Verification

- [ ] Start old backend:

```bash
cd /Users/siaxuan/Desktop/calendarAgent
.venv/bin/uvicorn main:app --reload
```

- [ ] Start Swift app:

```bash
cd /Users/siaxuan/Documents/Codex/2026-06-23/superpowers-brainstorming-users-siaxuan-codex-plugins
swift run ScheduleAgentApp
```

- [ ] Hover the right edge and confirm the sidebar opens.

- [ ] Confirm `Energy` uses backend `energy_curve`.

- [ ] Confirm `Today Queue` shows backend scheduled/suggested/instant blocks that can be synced.

- [ ] Confirm `Upcoming` shows fixed and scheduled blocks sorted by time.

- [ ] Click `Sync All`; expected backend call is `POST /schedule/{date}/write`.

- [ ] Click one task's secondary sync button; expected backend call is `POST /schedule/{date}/blocks/write` with the block start ISO string.

- [ ] Drag or move a backend-backed block; expected backend call is `POST /schedule/{date}/pin` with `block_key`.

- [ ] Stop the backend and relaunch Swift; expected result is mock fallback with a clear footer status.

---

## Deferred Work

- Document intake remains mock until the old backend has a document ingestion endpoint.
- SSE streaming is deferred; first pass can use `GET /schedule/{date}` and `POST /schedule/generate`.
- Native EventKit writeback is deferred; first pass writes through the old backend's CalDAV/iCloud writeback.
- Free-form task creation from the command field needs either `/chat/agent` routing or a new structured parse endpoint. Do not send raw text directly to `POST /tasks`.

---

## Self-Review

- Spec coverage: the plan preserves the existing web frontend, uses the old backend interfaces, keeps Swift visual UI, maps Energy/Today Queue/Upcoming, and documents sync + drag implementation details.
- Placeholder scan: no `TBD` or unspecified endpoint names remain.
- Type consistency: backend DTO names match the Swift files named in this plan; `block_key`, `start`, and schedule date behavior match the React API wrappers.
