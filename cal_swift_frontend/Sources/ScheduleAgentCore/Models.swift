import Foundation

public enum TaskPriority: Int, Codable, Sendable, CaseIterable {
    case low = 0
    case medium = 1
    case high = 2
    case urgent = 3
}

public enum EnergyType: String, Codable, Sendable, CaseIterable {
    case deepWork
    case shallowWork
    case admin
    case meetingPrep
}

public struct TaskItem: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var title: String
    public var estimatedMinutes: Int?
    public var deadline: Date?
    public var priority: TaskPriority
    public var project: String?
    public var isSplittable: Bool
    public var energy: EnergyType

    public init(
        id: UUID = UUID(),
        title: String,
        estimatedMinutes: Int? = nil,
        deadline: Date? = nil,
        priority: TaskPriority = .medium,
        project: String? = nil,
        isSplittable: Bool = false,
        energy: EnergyType = .shallowWork
    ) {
        self.id = id
        self.title = title
        self.estimatedMinutes = estimatedMinutes
        self.deadline = deadline
        self.priority = priority
        self.project = project
        self.isSplittable = isSplittable
        self.energy = energy
    }
}

public enum CalendarEventSource: String, Codable, Sendable {
    case appleCalendar
    case agent
    case imported
}

public struct CalendarEvent: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var title: String
    public var start: Date
    public var end: Date
    public var isMovable: Bool
    public var source: CalendarEventSource

    public init(
        id: UUID = UUID(),
        title: String,
        start: Date,
        end: Date,
        isMovable: Bool,
        source: CalendarEventSource
    ) {
        self.id = id
        self.title = title
        self.start = start
        self.end = end
        self.isMovable = isMovable
        self.source = source
    }
}

public struct UserPreferences: Codable, Equatable, Sendable {
    public var workdayStartHour: Int
    public var workdayEndHour: Int
    public var minimumBlockMinutes: Int
    public var bufferMinutes: Int
    public var deepWorkPreferredHours: Range<Int>

    public init(
        workdayStartHour: Int,
        workdayEndHour: Int,
        minimumBlockMinutes: Int,
        bufferMinutes: Int,
        deepWorkPreferredHours: Range<Int>
    ) {
        self.workdayStartHour = workdayStartHour
        self.workdayEndHour = workdayEndHour
        self.minimumBlockMinutes = minimumBlockMinutes
        self.bufferMinutes = bufferMinutes
        self.deepWorkPreferredHours = deepWorkPreferredHours
    }

    public static let standard = UserPreferences(
        workdayStartHour: 9,
        workdayEndHour: 17,
        minimumBlockMinutes: 30,
        bufferMinutes: 10,
        deepWorkPreferredHours: 9..<12
    )
}

public enum MemoryKind: String, Codable, Sendable {
    case preference
    case projectContext
    case personContext
    case lifeConstraint
    case feedback
}

public enum MemorySignal: String, Codable, Sendable {
    case preferDeepWorkInMorning
    case protectPersonalCommitments
    case addMeetingBuffer
    case generic
}

public struct MemoryEntry: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var kind: MemoryKind
    public var text: String
    public var signal: MemorySignal
    public var createdAt: Date

    public init(
        id: UUID = UUID(),
        kind: MemoryKind,
        text: String,
        signal: MemorySignal = .generic,
        createdAt: Date = .now
    ) {
        self.id = id
        self.kind = kind
        self.text = text
        self.signal = signal
        self.createdAt = createdAt
    }
}

public struct ScheduledBlock: Identifiable, Codable, Equatable, Sendable {
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

public struct ScheduleRisk: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var taskTitle: String
    public var reason: String

    public init(id: UUID = UUID(), taskTitle: String, reason: String) {
        self.id = id
        self.taskTitle = taskTitle
        self.reason = reason
    }
}

public struct SchedulePlan: Codable, Equatable, Sendable {
    public var scheduledBlocks: [ScheduledBlock]
    public var unscheduledTasks: [TaskItem]
    public var clarifyingQuestions: [String]
    public var risks: [ScheduleRisk]
    public var explanations: [String]

    public init(
        scheduledBlocks: [ScheduledBlock],
        unscheduledTasks: [TaskItem],
        clarifyingQuestions: [String],
        risks: [ScheduleRisk],
        explanations: [String]
    ) {
        self.scheduledBlocks = scheduledBlocks
        self.unscheduledTasks = unscheduledTasks
        self.clarifyingQuestions = clarifyingQuestions
        self.risks = risks
        self.explanations = explanations
    }
}

public struct PlanConfirmation: Codable, Equatable, Sendable {
    public var acceptedBlockIDs: Set<UUID>

    public init(acceptedBlockIDs: Set<UUID>) {
        self.acceptedBlockIDs = acceptedBlockIDs
    }
}
