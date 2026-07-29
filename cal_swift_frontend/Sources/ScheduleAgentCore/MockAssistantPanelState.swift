import Foundation

public struct ParsedTaskDraft: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var title: String
    public var estimatedMinutes: Int
    public var deadlineLabel: String
    public var priority: TaskPriority
    public var originalInput: String

    public init(
        id: UUID = UUID(),
        title: String,
        estimatedMinutes: Int,
        deadlineLabel: String,
        priority: TaskPriority,
        originalInput: String
    ) {
        self.id = id
        self.title = title
        self.estimatedMinutes = estimatedMinutes
        self.deadlineLabel = deadlineLabel
        self.priority = priority
        self.originalInput = originalInput
    }

    public func task(now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) -> TaskItem {
        let deadline: Date
        if deadlineLabel == "Tomorrow" {
            deadline = calendar.date(byAdding: .day, value: 1, to: now) ?? now.addingTimeInterval(24 * 60 * 60)
        } else {
            deadline = calendar.date(byAdding: .hour, value: 4, to: now) ?? now.addingTimeInterval(4 * 60 * 60)
        }

        return TaskItem(
            title: title,
            estimatedMinutes: estimatedMinutes,
            deadline: deadline,
            priority: priority,
            project: nil,
            isSplittable: false,
            energy: estimatedMinutes >= 90 ? .deepWork : .shallowWork
        )
    }
}

public struct MockPlanBlock: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var taskID: UUID
    public var taskTitle: String
    public var start: Date
    public var end: Date
    public var rationale: String

    public init(
        id: UUID = UUID(),
        taskID: UUID,
        taskTitle: String,
        start: Date,
        end: Date,
        rationale: String
    ) {
        self.id = id
        self.taskID = taskID
        self.taskTitle = taskTitle
        self.start = start
        self.end = end
        self.rationale = rationale
    }
}

public struct MockPlanDraft: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var blocks: [MockPlanBlock]
    public var summary: String
    public var risk: String?

    public init(id: UUID = UUID(), blocks: [MockPlanBlock], summary: String, risk: String? = nil) {
        self.id = id
        self.blocks = blocks
        self.summary = summary
        self.risk = risk
    }
}

public struct MockHealthSignal: Equatable, Sendable {
    public var hasExternalData: Bool
    public var energySource: DayflowEnergySource
    public var sleepWindow: String
    public var restingHeartRate: Int?
    public var hrv: Int?
    public var steps: Int?
    public var summary: String

    public init(
        hasExternalData: Bool,
        energySource: DayflowEnergySource = .none,
        sleepWindow: String,
        restingHeartRate: Int? = nil,
        hrv: Int? = nil,
        steps: Int? = nil,
        summary: String
    ) {
        self.hasExternalData = hasExternalData
        self.energySource = energySource
        self.sleepWindow = sleepWindow
        self.restingHeartRate = restingHeartRate
        self.hrv = hrv
        self.steps = steps
        self.summary = summary
    }
}

public struct DocumentIntakeStep: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var title: String
    public var isComplete: Bool

    public init(id: UUID = UUID(), title: String, isComplete: Bool) {
        self.id = id
        self.title = title
        self.isComplete = isComplete
    }
}

public enum DocumentRouteDestination: String, Equatable, Sendable, CaseIterable {
    case todayQueue
    case weeklyPlan
    case memory
    case clarification
}

public struct DocumentRouteOption: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var destination: DocumentRouteDestination
    public var title: String
    public var rationale: String

    public init(
        id: UUID = UUID(),
        destination: DocumentRouteDestination,
        title: String,
        rationale: String
    ) {
        self.id = id
        self.destination = destination
        self.title = title
        self.rationale = rationale
    }
}

public struct DocumentIntakeState: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var fileName: String
    public var steps: [DocumentIntakeStep]
    public var routes: [DocumentRouteOption]

    public init(
        id: UUID = UUID(),
        fileName: String,
        steps: [DocumentIntakeStep],
        routes: [DocumentRouteOption]
    ) {
        self.id = id
        self.fileName = fileName
        self.steps = steps
        self.routes = routes
    }
}

public struct DayflowTaskScheduleMetadata: Equatable, Sendable {
    public var start: Date
    public var end: Date
    public var focusMinutes: Int
    public var breakMinutes: Int
    public var pomodoroCount: Int
    public var taskKind: String?
    public var cognitiveLoad: DayflowCognitiveLoad?
    public var isDone: Bool

    public init(
        start: Date,
        end: Date,
        focusMinutes: Int,
        breakMinutes: Int,
        pomodoroCount: Int,
        taskKind: String? = nil,
        cognitiveLoad: DayflowCognitiveLoad? = nil,
        isDone: Bool = false
    ) {
        self.start = start
        self.end = end
        self.focusMinutes = focusMinutes
        self.breakMinutes = breakMinutes
        self.pomodoroCount = pomodoroCount
        self.taskKind = taskKind
        self.cognitiveLoad = cognitiveLoad
        self.isDone = isDone
    }

    public func durationMinutes(adjustingPomodoroCountBy delta: Int) -> Int {
        let focus = focusMinutes > 0 ? focusMinutes : 25
        let rest = breakMinutes > 0 ? breakMinutes : 5
        let nextCount = max(1, pomodoroCount + delta)
        return (nextCount * focus) + (max(0, nextCount - 1) * rest)
    }

    public var pomodoroSessionLabel: String {
        let focus = focusMinutes > 0 ? focusMinutes : 25
        let rest = breakMinutes > 0 ? breakMinutes : 5
        let count = max(1, pomodoroCount)
        let total = durationMinutes(adjustingPomodoroCountBy: 0)
        if count == 1 {
            return "1 x \(focus)m · \(focus)+\(rest)m"
        }
        return "\(count) x \(focus)m · \(total)m"
    }

    public var backendBadgeLabel: String? {
        if let taskKind = taskKind?.trimmingCharacters(in: .whitespacesAndNewlines),
           !taskKind.isEmpty {
            return taskKind.uppercased()
        }
        return cognitiveLoad?.rawValue.uppercased()
    }
}

public enum UpcomingLaneEntryKind: Equatable, Sendable {
    case agentTask
    case calendar
    case dropSlot
}

public struct UpcomingLaneEntry: Identifiable, Equatable, Sendable {
    public var id: UUID
    public var sourceID: UUID
    public var kind: UpcomingLaneEntryKind
    public var title: String
    public var start: Date
    public var end: Date
    public var task: TaskItem?
    public var event: CalendarEvent?
    public var metadata: DayflowTaskScheduleMetadata?
    public var isInProgress: Bool

    public init(
        id: UUID = UUID(),
        sourceID: UUID,
        kind: UpcomingLaneEntryKind,
        title: String,
        start: Date,
        end: Date,
        task: TaskItem? = nil,
        event: CalendarEvent? = nil,
        metadata: DayflowTaskScheduleMetadata? = nil,
        isInProgress: Bool
    ) {
        self.id = id
        self.sourceID = sourceID
        self.kind = kind
        self.title = title
        self.start = start
        self.end = end
        self.task = task
        self.event = event
        self.metadata = metadata
        self.isInProgress = isInProgress
    }
}

public struct MockAssistantPanelState: Equatable, Sendable {
    public var nowStatus: String
    public var nextEventSummary: String
    public var energyCurve: [Double]
    public var healthSignal: MockHealthSignal
    public var parsedTask: ParsedTaskDraft?
    public var documentIntake: DocumentIntakeState?
    public var todayQueue: [TaskItem]
    public var planDraft: MockPlanDraft?
    public var upcomingEvents: [CalendarEvent]
    public var statusMessage: String
    private var backendStartsByTaskID: [UUID: Date]
    private var backendBlockKeysByTaskID: [UUID: String]
    private var backendScheduleMetadataByTaskID: [UUID: DayflowTaskScheduleMetadata]
    private var syncedBackendBlockKeys: Set<String>

    public init(
        nowStatus: String,
        nextEventSummary: String,
        energyCurve: [Double],
        healthSignal: MockHealthSignal,
        parsedTask: ParsedTaskDraft? = nil,
        documentIntake: DocumentIntakeState? = nil,
        todayQueue: [TaskItem],
        planDraft: MockPlanDraft? = nil,
        upcomingEvents: [CalendarEvent],
        statusMessage: String,
        backendStartsByTaskID: [UUID: Date] = [:],
        backendBlockKeysByTaskID: [UUID: String] = [:],
        backendScheduleMetadataByTaskID: [UUID: DayflowTaskScheduleMetadata] = [:],
        syncedBackendBlockKeys: Set<String> = []
    ) {
        self.nowStatus = nowStatus
        self.nextEventSummary = nextEventSummary
        self.energyCurve = energyCurve
        self.healthSignal = healthSignal
        self.parsedTask = parsedTask
        self.documentIntake = documentIntake
        self.todayQueue = todayQueue
        self.planDraft = planDraft
        self.upcomingEvents = upcomingEvents
        self.statusMessage = statusMessage
        self.backendStartsByTaskID = backendStartsByTaskID
        self.backendBlockKeysByTaskID = backendBlockKeysByTaskID
        self.backendScheduleMetadataByTaskID = backendScheduleMetadataByTaskID
        self.syncedBackendBlockKeys = syncedBackendBlockKeys
    }

    public static func loading() -> MockAssistantPanelState {
        MockAssistantPanelState(
            nowStatus: "Connecting Dayflow",
            nextEventSummary: "Loading calendar and agent schedule",
            energyCurve: [],
            healthSignal: MockHealthSignal(
                hasExternalData: false,
                sleepWindow: "No data",
                summary: "Waiting for backend health and energy data."
            ),
            todayQueue: [],
            upcomingEvents: [],
            statusMessage: "Loading Dayflow backend schedule..."
        )
    }

    public static func sample(now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) -> MockAssistantPanelState {
        let firstStart = calendar.date(byAdding: .minute, value: -15, to: now) ?? now.addingTimeInterval(-15 * 60)
        let firstEnd = calendar.date(byAdding: .minute, value: 45, to: firstStart) ?? firstStart.addingTimeInterval(45 * 60)
        let secondStart = calendar.date(byAdding: .hour, value: 3, to: now) ?? now.addingTimeInterval(3 * 60 * 60)
        let secondEnd = calendar.date(byAdding: .minute, value: 45, to: secondStart) ?? secondStart.addingTimeInterval(45 * 60)

        return MockAssistantPanelState(
            nowStatus: "In Product review",
            nextEventSummary: "Until \(firstEnd.formatted(date: .omitted, time: .shortened)) · Next: Design sync",
            energyCurve: [
                0.30, 0.36, 0.49, 0.64, 0.78, 0.84,
                0.80, 0.66, 0.48, 0.40, 0.43, 0.57,
                0.73, 0.79, 0.68, 0.50, 0.34, 0.30,
                0.33, 0.40
            ],
            healthSignal: MockHealthSignal(
                hasExternalData: true,
                energySource: .today,
                sleepWindow: "00:42-07:28",
                restingHeartRate: 58,
                hrv: 62,
                steps: 4210,
                summary: "External health data loaded. Manual sleep input is locked."
            ),
            todayQueue: [
                TaskItem(
                    title: "Polish onboarding notes",
                    estimatedMinutes: 45,
                    deadline: calendar.date(byAdding: .day, value: 1, to: now),
                    priority: .medium,
                    energy: .shallowWork
                ),
                TaskItem(
                    title: "Outline agent sidebar UX",
                    estimatedMinutes: 90,
                    deadline: calendar.date(byAdding: .day, value: 1, to: now),
                    priority: .high,
                    energy: .deepWork
                )
            ],
            upcomingEvents: [
                CalendarEvent(title: "Product review", start: firstStart, end: firstEnd, isMovable: false, source: .appleCalendar),
                CalendarEvent(title: "Design sync", start: secondStart, end: secondEnd, isMovable: false, source: .appleCalendar)
            ],
            statusMessage: "Mock mode: no LLM calls, no calendar writes."
        )
    }

    public mutating func parseCommand(_ input: String, now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let lower = trimmed.lowercased()
        let minutes = extractMinutes(from: lower)
        let deadline = lower.contains("tomorrow") ? "Tomorrow" : "Today"
        let priority: TaskPriority = lower.contains("urgent") ? .urgent : lower.contains("high") ? .high : .medium
        let title = cleanTitle(from: trimmed)

        parsedTask = ParsedTaskDraft(
            title: title,
            estimatedMinutes: minutes,
            deadlineLabel: deadline,
            priority: priority,
            originalInput: trimmed
        )
        statusMessage = "Parsed a task draft. Review it before adding to queue."
    }

    public mutating func clearForBackendError(_ message: String) {
        nowStatus = "Backend unavailable"
        nextEventSummary = "Start Dayflow on localhost:8000"
        energyCurve = []
        healthSignal = MockHealthSignal(
            hasExternalData: false,
            sleepWindow: "No data",
            summary: "Backend schedule data is unavailable."
        )
        parsedTask = nil
        documentIntake = nil
        todayQueue = []
        planDraft = nil
        upcomingEvents = []
        backendStartsByTaskID = [:]
        backendBlockKeysByTaskID = [:]
        backendScheduleMetadataByTaskID = [:]
        statusMessage = message
    }

    public mutating func beginDayflowStream() {
        nowStatus = "Loading Dayflow"
        nextEventSummary = "Streaming health, calendar, and schedule"
        parsedTask = nil
        planDraft = nil
        todayQueue = []
        upcomingEvents = []
        backendStartsByTaskID = [:]
        backendBlockKeysByTaskID = [:]
        backendScheduleMetadataByTaskID = [:]
        statusMessage = "Streaming Dayflow schedule..."
    }

    public var shouldPromptForHealthInput: Bool {
        healthSignal.energySource == .none
    }

    public mutating func applyDayflowHealth(
        energyCurve: [Double],
        healthSummary: String,
        energySource: DayflowEnergySource = .today
    ) {
        self.energyCurve = energyCurve
        healthSignal = MockHealthSignal(
            hasExternalData: false,
            energySource: energySource,
            sleepWindow: healthSignal.sleepWindow,
            restingHeartRate: healthSignal.restingHeartRate,
            hrv: healthSignal.hrv,
            steps: healthSignal.steps,
            summary: healthSummary
        )
        statusMessage = energySource == .none
            ? "No energy data yet. Add sleep input to unlock the curve."
            : "Loaded energy curve from Dayflow."
    }

    public mutating func applyHealthSnapshot(
        sleepStart: Date,
        sleepEnd: Date,
        restingHeartRate: Int?,
        hrv: Int?,
        steps: Int?
    ) {
        healthSignal = MockHealthSignal(
            hasExternalData: false,
            energySource: healthSignal.energySource,
            sleepWindow: Self.sleepWindowLabel(start: sleepStart, end: sleepEnd),
            restingHeartRate: restingHeartRate,
            hrv: hrv,
            steps: steps,
            summary: healthSignal.summary
        )
    }

    public mutating func applyManualSleepWindow(sleepStart: Date, sleepEnd: Date) {
        healthSignal = MockHealthSignal(
            hasExternalData: false,
            energySource: .today,
            sleepWindow: Self.sleepWindowLabel(start: sleepStart, end: sleepEnd),
            restingHeartRate: healthSignal.restingHeartRate,
            hrv: healthSignal.hrv,
            steps: healthSignal.steps,
            summary: healthSignal.summary
        )
        statusMessage = "Updated manual sleep window."
    }

    public mutating func applyDayflowFixedBlocks(_ blocks: [DayflowScheduleBlock], now: Date = .now) {
        let events = blocks.map { block in
            CalendarEvent(
                title: block.title,
                start: block.start,
                end: block.end,
                isMovable: false,
                source: .appleCalendar
            )
        }
        loadCalendarEvents(events, now: now)
        statusMessage = "Loaded calendar anchors from Dayflow."
    }

    public mutating func applyDayflowScheduleBlocks(
        date: String,
        blocks: [DayflowScheduleBlock],
        unscheduled: [DayflowUnscheduledTask],
        now: Date = .now
    ) {
        let schedule = DayflowSchedule(
            date: date,
            energyCurve: energyCurve,
            energySource: healthSignal.energySource,
            blocks: blocks,
            unscheduled: unscheduled,
            healthSummary: healthSignal.summary
        )
        applyDayflowSchedule(schedule, now: now)
    }

    public mutating func addParsedTaskToQueue(now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        guard let parsedTask else { return }
        todayQueue.append(parsedTask.task(now: now, calendar: calendar))
        self.parsedTask = nil
        statusMessage = "Added task to Today Queue."
    }

    public func backendStart(for taskID: UUID) -> Date? {
        backendStartsByTaskID[taskID]
    }

    public func backendBlockKey(for taskID: UUID) -> String? {
        backendBlockKeysByTaskID[taskID]
    }

    public func backendScheduleMetadata(for taskID: UUID) -> DayflowTaskScheduleMetadata? {
        backendScheduleMetadataByTaskID[taskID]
    }

    public mutating func markBackendBlockSynced(_ blockKey: String) {
        syncedBackendBlockKeys.insert(blockKey)
    }

    public func isBackendBlockSynced(_ blockKey: String) -> Bool {
        syncedBackendBlockKeys.contains(blockKey)
    }

    public func upcomingLaneEntries(now: Date = .now, dropSlotEndHour: Int = 24) -> [UpcomingLaneEntry] {
        let calendarEntries = upcomingEvents.map { event in
            UpcomingLaneEntry(
                id: event.id,
                sourceID: event.id,
                kind: .calendar,
                title: event.title,
                start: event.start,
                end: event.end,
                event: event,
                isInProgress: event.start <= now && now < event.end
            )
        }

        let taskEntries = todayQueue.compactMap { task -> UpcomingLaneEntry? in
            guard let metadata = backendScheduleMetadataByTaskID[task.id] else { return nil }
            return UpcomingLaneEntry(
                id: task.id,
                sourceID: task.id,
                kind: .agentTask,
                title: task.title,
                start: metadata.start,
                end: metadata.end,
                task: task,
                metadata: metadata,
                isInProgress: metadata.start <= now && now < metadata.end
            )
        }

        let scheduledEntries = (calendarEntries + taskEntries).sorted {
            if $0.start == $1.start {
                return $0.title < $1.title
            }
            return $0.start < $1.start
        }

        return scheduledEntries + eveningDropSlots(after: scheduledEntries.last?.end, untilHour: dropSlotEndHour)
    }

    private func eveningDropSlots(after lastEnd: Date?, untilHour endHour: Int) -> [UpcomingLaneEntry] {
        guard let lastEnd else { return [] }
        let calendar = Calendar.current
        let dayStart = calendar.startOfDay(for: lastEnd)
        guard let end = calendar.date(byAdding: .hour, value: endHour, to: dayStart) else { return [] }
        var next = calendar.dateInterval(of: .hour, for: lastEnd)?.end ?? lastEnd
        if next <= lastEnd {
            next = calendar.date(byAdding: .hour, value: 1, to: next) ?? next
        }

        var slots: [UpcomingLaneEntry] = []
        while next < end {
            slots.append(
                UpcomingLaneEntry(
                    id: Self.dropSlotID(for: next),
                    sourceID: Self.dropSlotID(for: next),
                    kind: .dropSlot,
                    title: "Drop at \(Self.dropSlotTimeLabel(for: next))",
                    start: next,
                    end: next,
                    isInProgress: false
                )
            )
            guard let following = calendar.date(byAdding: .hour, value: 1, to: next) else { break }
            next = following
        }
        return slots
    }

    private static func dropSlotTimeLabel(for date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    private static func dropSlotID(for date: Date) -> UUID {
        let seconds = UInt64(max(0, Int(date.timeIntervalSince1970)))
        let bytes: [UInt8] = [
            0xD0, 0xD0, 0x50, 0x10,
            UInt8((seconds >> 56) & 0xff),
            UInt8((seconds >> 48) & 0xff),
            UInt8((seconds >> 40) & 0xff),
            UInt8((seconds >> 32) & 0xff),
            UInt8((seconds >> 24) & 0xff),
            UInt8((seconds >> 16) & 0xff),
            UInt8((seconds >> 8) & 0xff),
            UInt8(seconds & 0xff),
            0xA7, 0xA7, 0xA7, 0xA7
        ]
        return UUID(uuid: (
            bytes[0], bytes[1], bytes[2], bytes[3],
            bytes[4], bytes[5], bytes[6], bytes[7],
            bytes[8], bytes[9], bytes[10], bytes[11],
            bytes[12], bytes[13], bytes[14], bytes[15]
        ))
    }

    /// Optimistically flip a task's done state so the checkmark responds
    /// immediately; the caller persists via the backend complete endpoint.
    public mutating func setTaskDone(_ taskID: UUID, done: Bool) {
        guard var metadata = backendScheduleMetadataByTaskID[taskID] else { return }
        metadata.isDone = done
        backendScheduleMetadataByTaskID[taskID] = metadata
    }

    public func isTaskDone(_ taskID: UUID) -> Bool {
        backendScheduleMetadataByTaskID[taskID]?.isDone ?? false
    }

    public mutating func adjustBackendScheduleMetadata(for taskID: UUID, durationMinutes: Int, pomodoroDelta: Int) {
        guard var metadata = backendScheduleMetadataByTaskID[taskID] else { return }
        metadata.end = metadata.start.addingTimeInterval(TimeInterval(max(5, durationMinutes) * 60))
        metadata.pomodoroCount = max(1, metadata.pomodoroCount + pomodoroDelta)
        backendScheduleMetadataByTaskID[taskID] = metadata
        if let index = todayQueue.firstIndex(where: { $0.id == taskID }) {
            todayQueue[index].estimatedMinutes = durationMinutes
        }
    }

    public mutating func applyDayflowSchedule(_ schedule: DayflowSchedule, now: Date = .now) {
        energyCurve = schedule.energyCurve
        healthSignal = MockHealthSignal(
            hasExternalData: false,
            energySource: schedule.energySource,
            sleepWindow: healthSignal.sleepWindow,
            restingHeartRate: healthSignal.restingHeartRate,
            hrv: healthSignal.hrv,
            steps: healthSignal.steps,
            summary: schedule.healthSummary
        )

        var nextQueue: [TaskItem] = []
        var starts: [UUID: Date] = [:]
        var keys: [UUID: String] = [:]
        var metadata: [UUID: DayflowTaskScheduleMetadata] = [:]
        var calendarEvents: [CalendarEvent] = []

        for block in schedule.blocks {
            switch block.blockType {
            case .fixed, .meal:
                calendarEvents.append(CalendarEvent(
                    title: block.title,
                    start: block.start,
                    end: block.end,
                    isMovable: false,
                    source: .appleCalendar
                ))
            case .suggested, .scheduled, .instant:
                let task = TaskItem(
                    title: block.displayTitle,   // carried blocks read "继续：X"
                    estimatedMinutes: max(5, Int(block.end.timeIntervalSince(block.start) / 60)),
                    deadline: block.deadline,
                    priority: .medium,
                    project: block.phaseLabel,
                    isSplittable: false,
                    energy: energyType(for: block.cognitiveLoad)
                )
                nextQueue.append(task)
                starts[task.id] = block.start
                metadata[task.id] = DayflowTaskScheduleMetadata(
                    start: block.start,
                    end: block.end,
                    focusMinutes: block.focusMinutes,
                    breakMinutes: block.breakMinutes,
                    pomodoroCount: block.pomodoroCount,
                    taskKind: block.taskKind,
                    cognitiveLoad: block.cognitiveLoad,
                    isDone: block.isDone
                )
                if let backendID = block.taskID {
                    keys[task.id] = "\(backendID)::\(block.title)"
                }
            }
        }

        for task in schedule.unscheduled {
            nextQueue.append(TaskItem(
                title: task.title,
                estimatedMinutes: task.estimatedMinutes,
                deadline: task.deadline,
                priority: .medium,
                project: task.phaseLabel,
                isSplittable: false,
                energy: energyType(for: task.cognitiveLoad)
            ))
        }

        todayQueue = nextQueue
        backendStartsByTaskID = starts
        backendBlockKeysByTaskID = keys
        backendScheduleMetadataByTaskID = metadata
        loadCalendarEvents(calendarEvents, now: now)
        statusMessage = "Loaded Dayflow agent schedule for \(schedule.date)."
    }

    private static func sleepWindowLabel(start: Date, end: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "HH:mm"
        return "\(formatter.string(from: start))-\(formatter.string(from: end))"
    }

    public mutating func schedule(taskID: UUID, now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        let tasks = todayQueue.filter { $0.id == taskID }
        createDraft(for: tasks, now: now, calendar: calendar)
    }

    public mutating func planQueue(now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        createDraft(for: todayQueue, now: now, calendar: calendar)
    }

    public mutating func planQueueWithAgent(
        now: Date = .now,
        memories: [MemoryEntry] = [
            MemoryEntry(
                kind: .preference,
                text: "Deep work usually lands best before lunch.",
                signal: .preferDeepWorkInMorning
            )
        ],
        preferences: UserPreferences = .standard,
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) {
        createAgentDraft(for: todayQueue, now: now, memories: memories, preferences: preferences, calendar: calendar)
    }

    public mutating func planTaskWithAgent(
        taskID: UUID,
        now: Date = .now,
        memories: [MemoryEntry] = [
            MemoryEntry(
                kind: .preference,
                text: "Deep work usually lands best before lunch.",
                signal: .preferDeepWorkInMorning
            )
        ],
        preferences: UserPreferences = .standard,
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) {
        createAgentDraft(
            for: todayQueue.filter { $0.id == taskID },
            now: now,
            memories: memories,
            preferences: preferences,
            calendar: calendar
        )
    }

    public mutating func syncQueueToCalendar(now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        createDraft(for: todayQueue, now: now, calendar: calendar)
        confirmDraft()
    }

    public mutating func acceptSingleTask(taskID: UUID, now: Date = .now, calendar: Calendar = Calendar(identifier: .gregorian)) {
        let tasks = todayQueue.filter { $0.id == taskID }
        createDraft(for: tasks, now: now, calendar: calendar)
        confirmDraft()
    }

    public mutating func moveDraftBlock(taskID: UUID, toStart start: Date) {
        guard var draft = planDraft,
              let index = draft.blocks.firstIndex(where: { $0.taskID == taskID }),
              let task = todayQueue.first(where: { $0.id == taskID })
        else {
            return
        }

        let minutes = task.estimatedMinutes ?? 30
        draft.blocks[index].start = start
        draft.blocks[index].end = start.addingTimeInterval(TimeInterval(minutes * 60))
        draft.blocks[index].rationale = "Mock moved by dragging onto the timeline."
        draft.summary = "Adjusted \(draft.blocks[index].taskTitle) to \(start.formatted(date: .omitted, time: .shortened))."
        planDraft = draft
        statusMessage = "Moved draft block. Confirm to sync it into Upcoming Calendar."
    }

    public mutating func startDocumentIntake(fileName: String) {
        documentIntake = DocumentIntakeState(
            fileName: fileName,
            steps: [
                DocumentIntakeStep(title: "Read", isComplete: true),
                DocumentIntakeStep(title: "Classify", isComplete: true),
                DocumentIntakeStep(title: "Reality check", isComplete: true),
                DocumentIntakeStep(title: "Route", isComplete: true)
            ],
            routes: [
                DocumentRouteOption(
                    destination: .todayQueue,
                    title: "Schedule syllabus milestones",
                    rationale: "Detected dated assignments that can become task blocks."
                ),
                DocumentRouteOption(
                    destination: .weeklyPlan,
                    title: "Add course workload to week plan",
                    rationale: "Several deadlines need distribution across the week."
                ),
                DocumentRouteOption(
                    destination: .memory,
                    title: "Remember course constraints",
                    rationale: "Office hours and grading policies look reusable."
                ),
                DocumentRouteOption(
                    destination: .clarification,
                    title: "Ask which section matters",
                    rationale: "Some dates conflict and need user confirmation."
                )
            ]
        )
        statusMessage = "Mock intake complete. Review agent routing options."
    }

    public mutating func loadCalendarEvents(_ events: [CalendarEvent], now: Date = .now) {
        upcomingEvents = events.sorted {
            if $0.start == $1.start {
                return $0.title < $1.title
            }
            return $0.start < $1.start
        }

        let agenda = CalendarAgenda(events: upcomingEvents)
        let current = agenda.current(at: now)
        let upcoming = agenda.upcoming(after: now)
        let next = upcoming.first { event in
            if let current {
                return event.id != current.id
            }
            return event.start >= now
        }

        if let current {
            nowStatus = "In \(current.title)"
            if let next {
                nextEventSummary = "Until \(current.end.formatted(date: .omitted, time: .shortened)) · Next: \(next.title)"
            } else {
                nextEventSummary = "Until \(current.end.formatted(date: .omitted, time: .shortened))"
            }
        } else if let next {
            nowStatus = "Free until \(next.start.formatted(date: .omitted, time: .shortened))"
            nextEventSummary = "Next: \(next.title) at \(next.start.formatted(date: .omitted, time: .shortened))"
        } else {
            nowStatus = "Free"
            nextEventSummary = "No upcoming calendar events"
        }

        statusMessage = "Loaded \(events.count) Apple Calendar event\(events.count == 1 ? "" : "s")."
    }

    public mutating func confirmDraft() {
        guard let planDraft else { return }
        let taskIDs = Set(planDraft.blocks.map(\.taskID))
        for block in planDraft.blocks {
            upcomingEvents.append(CalendarEvent(
                title: block.taskTitle,
                start: block.start,
                end: block.end,
                isMovable: true,
                source: .agent
            ))
        }
        todayQueue.removeAll { taskIDs.contains($0.id) }
        upcomingEvents.sort {
            if $0.start == $1.start {
                return $0.title < $1.title
            }
            return $0.start < $1.start
        }
        self.planDraft = nil
        statusMessage = "Mock confirmed: moved scheduled task(s) into Upcoming Calendar."
    }

    public mutating func dismissDraft() {
        planDraft = nil
        statusMessage = "Dismissed draft. Today Queue is unchanged."
    }

    private mutating func createAgentDraft(
        for tasks: [TaskItem],
        now: Date,
        memories: [MemoryEntry],
        preferences: UserPreferences,
        calendar: Calendar
    ) {
        guard !tasks.isEmpty else {
            planDraft = nil
            statusMessage = "Nothing to schedule yet."
            return
        }

        let planningWindow = PlanningWindowFactory(calendar: calendar).window(containing: now, preferences: preferences)
        let plan = SchedulingEngine(calendar: calendar).proposeSchedule(
            tasks: tasks,
            events: upcomingEvents,
            memories: memories,
            preferences: preferences,
            planningWindow: planningWindow
        )

        planDraft = MockPlanDraft(
            blocks: plan.scheduledBlocks.map { block in
                MockPlanBlock(
                    id: block.id,
                    taskID: block.taskID,
                    taskTitle: block.taskTitle,
                    start: block.start,
                    end: block.end,
                    rationale: block.rationale
                )
            },
            summary: "Agent drafted \(plan.scheduledBlocks.count) block\(plan.scheduledBlocks.count == 1 ? "" : "s") around your real calendar.",
            risk: plan.risks.first?.reason ?? plan.clarifyingQuestions.first
        )
        statusMessage = plan.scheduledBlocks.isEmpty
            ? "Agent could not fit the queue yet. Review risks or missing details."
            : "Agent draft ready using real Apple Calendar busy blocks."
    }

    private mutating func createDraft(for tasks: [TaskItem], now: Date, calendar: Calendar) {
        guard !tasks.isEmpty else {
            planDraft = nil
            statusMessage = "Nothing to schedule yet."
            return
        }

        var cursor = calendar.date(byAdding: .minute, value: 30, to: now) ?? now.addingTimeInterval(30 * 60)
        var blocks: [MockPlanBlock] = []
        for task in tasks {
            let minutes = task.estimatedMinutes ?? 30
            let end = calendar.date(byAdding: .minute, value: minutes, to: cursor) ?? cursor.addingTimeInterval(TimeInterval(minutes * 60))
            blocks.append(MockPlanBlock(
                taskID: task.id,
                taskTitle: task.title,
                start: cursor,
                end: end,
                rationale: "Mock scheduled into the next open planning window."
            ))
            cursor = calendar.date(byAdding: .minute, value: 15, to: end) ?? end.addingTimeInterval(15 * 60)
        }

        planDraft = MockPlanDraft(
            blocks: blocks,
            summary: "Drafted \(blocks.count) task block\(blocks.count == 1 ? "" : "s") for review.",
            risk: blocks.count > 2 ? "This may crowd the afternoon; review before confirming." : nil
        )
        statusMessage = "Draft ready. Confirm to mock-move into Upcoming Calendar."
    }

    private func extractMinutes(from lowercasedInput: String) -> Int {
        let words = lowercasedInput.split(separator: " ")
        for word in words {
            if word.hasSuffix("h"), let hours = Double(word.dropLast()) {
                return Int(hours * 60)
            }
            if word.hasSuffix("m"), let minutes = Int(word.dropLast()) {
                return minutes
            }
        }
        return 45
    }

    private func energyType(for load: DayflowCognitiveLoad?) -> EnergyType {
        switch load {
        case .deep:
            .deepWork
        case .light, .medium, nil:
            .shallowWork
        }
    }

    private func cleanTitle(from input: String) -> String {
        var parts = input.split(separator: " ").map(String.init)
        parts.removeAll { part in
            let lower = part.lowercased()
            return lower == "tomorrow"
                || lower == "today"
                || lower == "urgent"
                || lower == "high"
                || lower.hasSuffix("h")
                || lower.hasSuffix("m")
        }
        return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
