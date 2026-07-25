import Foundation

public enum DayflowBlockType: String, Codable, Sendable {
    case fixed
    case meal
    case suggested
    case scheduled
    case instant
}

public enum DayflowCognitiveLoad: String, Codable, Sendable {
    case light
    case medium
    case deep
}

public struct DayflowScheduleBlock: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var start: Date
    public var end: Date
    public var blockType: DayflowBlockType
    public var taskID: String?
    public var title: String
    public var cognitiveLoad: DayflowCognitiveLoad?
    public var taskKind: String?
    public var notes: String?
    public var phaseLabel: String?
    public var focusMinutes: Int
    public var breakMinutes: Int
    public var pomodoroCount: Int
    public var deadline: Date?
    /// An unfinished project chunk rolled over from a past day. The UI prefixes
    /// the label with "继续：" so the day reads "继续昨天没做完的 X".
    public var carriedOver: Bool

    /// Title as shown to the user — carried blocks get the "继续：" prefix.
    public var displayTitle: String {
        carriedOver ? "继续：\(title)" : title
    }

    public init(
        id: UUID = UUID(),
        start: Date,
        end: Date,
        blockType: DayflowBlockType,
        taskID: String?,
        title: String,
        cognitiveLoad: DayflowCognitiveLoad?,
        taskKind: String? = nil,
        notes: String?,
        phaseLabel: String?,
        focusMinutes: Int,
        breakMinutes: Int,
        pomodoroCount: Int,
        deadline: Date?,
        carriedOver: Bool = false
    ) {
        self.id = id
        self.start = start
        self.end = end
        self.blockType = blockType
        self.taskID = taskID
        self.title = title
        self.cognitiveLoad = cognitiveLoad
        self.taskKind = taskKind
        self.notes = notes
        self.phaseLabel = phaseLabel
        self.focusMinutes = focusMinutes
        self.breakMinutes = breakMinutes
        self.pomodoroCount = pomodoroCount
        self.deadline = deadline
        self.carriedOver = carriedOver
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
        case carriedOver = "carried_over"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        start = try container.decode(Date.self, forKey: .start)
        end = try container.decode(Date.self, forKey: .end)
        blockType = try container.decode(DayflowBlockType.self, forKey: .blockType)
        taskID = try container.decodeIfPresent(String.self, forKey: .taskID)
        title = try container.decode(String.self, forKey: .title)
        cognitiveLoad = try container.decodeIfPresent(DayflowCognitiveLoad.self, forKey: .cognitiveLoad)
        taskKind = try container.decodeIfPresent(String.self, forKey: .taskKind)
        notes = try container.decodeIfPresent(String.self, forKey: .notes)
        phaseLabel = try container.decodeIfPresent(String.self, forKey: .phaseLabel)
        focusMinutes = try container.decode(Int.self, forKey: .focusMinutes)
        breakMinutes = try container.decode(Int.self, forKey: .breakMinutes)
        pomodoroCount = try container.decode(Int.self, forKey: .pomodoroCount)
        deadline = try container.decodeIfPresent(Date.self, forKey: .deadline)
        carriedOver = try container.decodeIfPresent(Bool.self, forKey: .carriedOver) ?? false
        id = UUID()
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(start, forKey: .start)
        try container.encode(end, forKey: .end)
        try container.encode(blockType, forKey: .blockType)
        try container.encodeIfPresent(taskID, forKey: .taskID)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(cognitiveLoad, forKey: .cognitiveLoad)
        try container.encodeIfPresent(taskKind, forKey: .taskKind)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encodeIfPresent(phaseLabel, forKey: .phaseLabel)
        try container.encode(focusMinutes, forKey: .focusMinutes)
        try container.encode(breakMinutes, forKey: .breakMinutes)
        try container.encode(pomodoroCount, forKey: .pomodoroCount)
        try container.encodeIfPresent(deadline, forKey: .deadline)
        try container.encode(carriedOver, forKey: .carriedOver)
    }
}

public struct DayflowUnscheduledTask: Codable, Equatable, Sendable {
    public var parentID: String
    public var title: String
    public var cognitiveLoad: DayflowCognitiveLoad?
    public var estimatedMinutes: Int
    public var phaseLabel: String?
    public var deadline: Date?

    public init(
        parentID: String,
        title: String,
        cognitiveLoad: DayflowCognitiveLoad?,
        estimatedMinutes: Int,
        phaseLabel: String?,
        deadline: Date?
    ) {
        self.parentID = parentID
        self.title = title
        self.cognitiveLoad = cognitiveLoad
        self.estimatedMinutes = estimatedMinutes
        self.phaseLabel = phaseLabel
        self.deadline = deadline
    }

    enum CodingKeys: String, CodingKey {
        case parentID = "parent_id"
        case title
        case cognitiveLoad = "cognitive_load"
        case estimatedMinutes = "estimated_minutes"
        case phaseLabel = "phase_label"
        case deadline
    }
}

public struct DayflowSchedule: Codable, Equatable, Sendable {
    public var date: String
    public var energyCurve: [Double]
    public var energySource: DayflowEnergySource
    public var blocks: [DayflowScheduleBlock]
    public var unscheduled: [DayflowUnscheduledTask]
    public var healthSummary: String

    public init(
        date: String,
        energyCurve: [Double],
        energySource: DayflowEnergySource = .today,
        blocks: [DayflowScheduleBlock],
        unscheduled: [DayflowUnscheduledTask],
        healthSummary: String
    ) {
        self.date = date
        self.energyCurve = energyCurve
        self.energySource = energySource
        self.blocks = blocks
        self.unscheduled = unscheduled
        self.healthSummary = healthSummary
    }

    enum CodingKeys: String, CodingKey {
        case date
        case energyCurve = "energy_curve"
        case energySource = "energy_source"
        case blocks
        case unscheduled
        case healthSummary = "health_summary"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decode(String.self, forKey: .date)
        energyCurve = try container.decode([Double].self, forKey: .energyCurve)
        energySource = try container.decodeIfPresent(DayflowEnergySource.self, forKey: .energySource) ?? .today
        blocks = try container.decode([DayflowScheduleBlock].self, forKey: .blocks)
        unscheduled = try container.decode([DayflowUnscheduledTask].self, forKey: .unscheduled)
        healthSummary = try container.decode(String.self, forKey: .healthSummary)
    }
}

public enum DayflowEnergySource: String, Codable, Equatable, Sendable {
    case today
    case baseline
    case none
}

public enum DayflowStreamEvent: Equatable, Sendable {
    case health(energyCurve: [Double], healthSummary: String, energySource: DayflowEnergySource)
    case fixed(blocks: [DayflowScheduleBlock])
    case schedule(blocks: [DayflowScheduleBlock], unscheduled: [DayflowUnscheduledTask])
    case done
    case error(message: String)
}

extension DayflowStreamEvent: Decodable {
    private enum CodingKeys: String, CodingKey {
        case type
        case energyCurve = "energy_curve"
        case energySource = "energy_source"
        case healthSummary = "health_summary"
        case blocks
        case unscheduled
        case message
    }

    private enum EventType: String, Decodable {
        case health
        case fixed
        case schedule
        case done
        case error
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(EventType.self, forKey: .type)
        switch type {
        case .health:
            self = .health(
                energyCurve: try container.decode([Double].self, forKey: .energyCurve),
                healthSummary: try container.decode(String.self, forKey: .healthSummary),
                energySource: try container.decodeIfPresent(DayflowEnergySource.self, forKey: .energySource) ?? .today
            )
        case .fixed:
            self = .fixed(blocks: try container.decode([DayflowScheduleBlock].self, forKey: .blocks))
        case .schedule:
            self = .schedule(
                blocks: try container.decode([DayflowScheduleBlock].self, forKey: .blocks),
                unscheduled: try container.decode([DayflowUnscheduledTask].self, forKey: .unscheduled)
            )
        case .done:
            self = .done
        case .error:
            self = .error(message: try container.decode(String.self, forKey: .message))
        }
    }
}
