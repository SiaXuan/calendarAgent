import AppKit
import EventKit
import Foundation
import ScheduleAgentCore

final class AppleCalendarAdapter {
    private let store: EKEventStore

    init(store: EKEventStore = EKEventStore()) {
        self.store = store
    }

    func requestAccess(completion: @escaping (Bool, Error?) -> Void) {
        if #available(macOS 14.0, *) {
            store.requestFullAccessToEvents(completion: completion)
        } else {
            store.requestAccess(to: .event, completion: completion)
        }
    }

    func events(in interval: DateInterval) -> [CalendarEvent] {
        let predicate = store.predicateForEvents(
            withStart: interval.start,
            end: interval.end,
            calendars: nil
        )

        return store.events(matching: predicate).map { event in
            CalendarEvent(
                id: UUID(),
                title: event.title ?? "Untitled event",
                start: event.startDate,
                end: event.endDate,
                isMovable: false,
                source: .appleCalendar
            )
        }
    }

    @discardableResult
    func write(blocks: [MockPlanBlock]) throws -> Int {
        let calendar = try writableCalendar()
        for block in blocks {
            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = "Agent: \(block.taskTitle)"
            event.startDate = block.start
            event.endDate = block.end
            event.notes = block.rationale
            try store.save(event, span: .thisEvent)
        }
        return blocks.count
    }

    func openInCalendar(near date: Date) {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Calendar.app"))
    }

    private func writableCalendar() throws -> EKCalendar {
        let calendarTitle = "Schedule Agent"
        if let existing = store.calendars(for: .event).first(where: { $0.title == calendarTitle && $0.allowsContentModifications }) {
            return existing
        }

        let calendar = EKCalendar(for: .event, eventStore: store)
        calendar.title = calendarTitle
        calendar.source = store.defaultCalendarForNewEvents?.source ?? store.sources.first
        try store.saveCalendar(calendar, commit: true)
        return calendar
    }
}

final class AppleCalendarEventWriter: CalendarWriting {
    private let store: EKEventStore
    private let calendarTitle: String

    init(store: EKEventStore = EKEventStore(), calendarTitle: String = "Schedule Agent") {
        self.store = store
        self.calendarTitle = calendarTitle
    }

    func write(plan: SchedulePlan, confirmation: PlanConfirmation?) throws {
        guard let confirmation else {
            throw CalendarWriteError.planNotConfirmed
        }

        let calendar = try writableCalendar()
        for block in plan.scheduledBlocks where confirmation.acceptedBlockIDs.contains(block.id) {
            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = "Agent: \(block.taskTitle)"
            event.startDate = block.start
            event.endDate = block.end
            event.notes = block.rationale
            try store.save(event, span: .thisEvent)
        }
    }

    private func writableCalendar() throws -> EKCalendar {
        if let existing = store.calendars(for: .event).first(where: { $0.title == calendarTitle && $0.allowsContentModifications }) {
            return existing
        }

        let calendar = EKCalendar(for: .event, eventStore: store)
        calendar.title = calendarTitle
        calendar.source = store.defaultCalendarForNewEvents?.source ?? store.sources.first
        try store.saveCalendar(calendar, commit: true)
        return calendar
    }
}
