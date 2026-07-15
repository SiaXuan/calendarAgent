import Foundation

public struct PrivacyContextBuilder: Sendable {
    private let calendar: Calendar

    public init(calendar: Calendar = Calendar(identifier: .gregorian)) {
        self.calendar = calendar
    }

    public func build(
        tasks: [TaskItem],
        events: [CalendarEvent],
        memories: [MemoryEntry]
    ) -> AgentPromptContext {
        AgentPromptContext(
            tasks: tasks,
            calendarSummary: summarize(events: events),
            memories: memories
        )
    }

    private func summarize(events: [CalendarEvent]) -> String {
        guard !events.isEmpty else {
            return "No busy blocks in the planning window."
        }

        let ranges = events
            .sorted { $0.start < $1.start }
            .map { event in
                "\(event.start.formatted(date: .omitted, time: .shortened))-\(event.end.formatted(date: .omitted, time: .shortened))"
            }
            .joined(separator: ", ")

        let noun = events.count == 1 ? "busy block" : "busy blocks"
        return "\(events.count) \(noun): \(ranges). Event titles are withheld for privacy."
    }
}
