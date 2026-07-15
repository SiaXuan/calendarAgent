import Foundation

public struct AgentPromptContext: Codable, Equatable, Sendable {
    public var tasks: [TaskItem]
    public var calendarSummary: String
    public var memories: [MemoryEntry]

    public init(tasks: [TaskItem], calendarSummary: String, memories: [MemoryEntry]) {
        self.tasks = tasks
        self.calendarSummary = calendarSummary
        self.memories = memories
    }
}

public struct AgentSuggestion: Codable, Equatable, Sendable {
    public var message: String
    public var inferredMemories: [MemoryEntry]

    public init(message: String, inferredMemories: [MemoryEntry] = []) {
        self.message = message
        self.inferredMemories = inferredMemories
    }
}

public protocol LLMAgentCore {
    func suggestNextStep(context: AgentPromptContext, draftPlan: SchedulePlan) async throws -> AgentSuggestion
}

public struct LocalFallbackAgent: LLMAgentCore, Sendable {
    public init() {}

    public func suggestNextStep(context: AgentPromptContext, draftPlan: SchedulePlan) async throws -> AgentSuggestion {
        if let question = draftPlan.clarifyingQuestions.first {
            return AgentSuggestion(message: question)
        }
        if let risk = draftPlan.risks.first {
            return AgentSuggestion(message: "Risk: \(risk.reason)")
        }
        return AgentSuggestion(message: "I drafted \(draftPlan.scheduledBlocks.count) calendar block(s) for review.")
    }
}
