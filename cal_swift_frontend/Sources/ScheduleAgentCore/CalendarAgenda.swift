import Foundation

public struct CalendarAgenda: Sendable {
    private var events: [CalendarEvent]

    public init(events: [CalendarEvent]) {
        self.events = events
    }

    public func upcoming(after date: Date = .now, limit: Int? = nil) -> [CalendarEvent] {
        let sorted = events
            .filter { $0.end > date }
            .sorted { lhs, rhs in
                if lhs.start == rhs.start {
                    return lhs.title < rhs.title
                }
                return lhs.start < rhs.start
            }

        guard let limit else {
            return sorted
        }
        return Array(sorted.prefix(limit))
    }

    public func current(at date: Date = .now) -> CalendarEvent? {
        events
            .filter { $0.start <= date && $0.end > date }
            .sorted { lhs, rhs in
                if lhs.start == rhs.start {
                    return lhs.title < rhs.title
                }
                return lhs.start < rhs.start
            }
            .first
    }
}
