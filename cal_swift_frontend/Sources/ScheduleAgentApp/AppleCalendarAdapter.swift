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

// ─── Phase 4 EventKit executor (docs/ARCHITECTURE.md §0) ─────────────────────
//
// The backend never touches the OS calendar/reminders. It returns change-sets
// {create, update, delete}; this layer reads the current agent-owned items
// (identified by the per-item tag embedded in the notes), uploads them so the
// backend can diff, and applies the returned change-set — always delete-before-
// create for a matched key so a failed delete can't leave a duplicate.

/// Parses the identity tags the backend embeds in event/reminder notes.
enum AgentTag {
    static let scheduledPrefix = "[agent-scheduled:dayflow:"
    static let reminderPrefix = "[agent-reminder:dayflow:"
    static let projectPrefix = "[agent-project:"

    /// The value between `prefix` and the next `]`, e.g. the tag_key hash.
    static func value(in notes: String?, prefix: String) -> String? {
        guard let notes, let start = notes.range(of: prefix) else { return nil }
        let rest = notes[start.upperBound...]
        guard let end = rest.firstIndex(of: "]") else { return nil }
        return String(rest[..<end])
    }
}

extension AppleCalendarAdapter {

    // MARK: Access (events + reminders)

    /// Request full access to both calendars and reminders. Must be granted
    /// before any read/apply call. Returns whether BOTH were granted.
    func requestFullAccess() async -> (granted: Bool, error: Error?) {
        await withCheckedContinuation { continuation in
            if #available(macOS 14.0, *) {
                self.store.requestFullAccessToEvents { okEvents, errEvents in
                    self.store.requestFullAccessToReminders { okReminders, errReminders in
                        continuation.resume(returning: (okEvents && okReminders, errEvents ?? errReminders))
                    }
                }
            } else {
                self.store.requestAccess(to: .event) { okEvents, errEvents in
                    self.store.requestAccess(to: .reminder) { okReminders, errReminders in
                        continuation.resume(returning: (okEvents && okReminders, errEvents ?? errReminders))
                    }
                }
            }
        }
    }

    // MARK: Reads → upload to the backend

    /// The agent-owned events currently on the calendar for `date`, as the
    /// backend's changeset input. Only ACTIVE (scheduled) blocks — user events
    /// and history blocks are left out (the backend doesn't reconcile them).
    func currentAgentEvents(on date: Date) -> [DayflowCurrentEventInput] {
        agentActiveEventsByTagKey(on: date).compactMap { tagKey, events in
            guard let event = events.first else { return nil }
            return DayflowCurrentEventInput(
                tagKey: tagKey,
                title: event.title,
                start: Self.iso(event.startDate),
                end: Self.iso(event.endDate))
        }
    }

    /// The agent-owned reminders currently in Reminders for `projectID` (nil =
    /// any project), as the backend's replan input.
    func currentAgentReminders(forProject projectID: String? = nil) async -> [DayflowCurrentReminderInput] {
        let reminders = await fetchReminders()
        var out: [DayflowCurrentReminderInput] = []
        for r in reminders {
            guard let tagKey = AgentTag.value(in: r.notes, prefix: AgentTag.reminderPrefix) else { continue }
            if let projectID,
               AgentTag.value(in: r.notes, prefix: AgentTag.projectPrefix) != projectID { continue }
            out.append(DayflowCurrentReminderInput(tagKey: tagKey, title: r.title, due: nil))
        }
        return out
    }

    // MARK: Apply change-sets

    /// Apply a today's-time-blocks change-set to the calendar (delete-before-create).
    func applyEventChangeset(_ changeset: DayflowEventChangeset, on date: Date) throws {
        let calendar = try eventCalendar()
        let byTag = agentActiveEventsByTagKey(on: date)

        for ref in changeset.delete {
            for event in byTag[ref.tagKey] ?? [] { try store.remove(event, span: .thisEvent, commit: false) }
        }
        for spec in changeset.update {
            for event in byTag[spec.tagKey] ?? [] { try store.remove(event, span: .thisEvent, commit: false) }
        }
        for spec in changeset.update + changeset.create {
            guard let start = Self.parse(spec.start), let end = Self.parse(spec.end) else { continue }
            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = spec.title
            event.startDate = start
            event.endDate = end
            event.notes = spec.notes ?? spec.tag
            try store.save(event, span: .thisEvent, commit: false)
        }
        try store.commit()
    }

    /// Apply a project's reminders change-set (delete-before-create).
    func applyReminderChangeset(_ changeset: DayflowReminderChangeset) async throws {
        let calendar = try reminderCalendar()
        let existing = await fetchReminders()
        var byTag: [String: [EKReminder]] = [:]
        for r in existing {
            if let key = AgentTag.value(in: r.notes, prefix: AgentTag.reminderPrefix) {
                byTag[key, default: []].append(r)
            }
        }

        for ref in changeset.delete {
            for r in byTag[ref.tagKey] ?? [] { try store.remove(r, commit: false) }
        }
        for spec in changeset.update {
            for r in byTag[spec.tagKey] ?? [] { try store.remove(r, commit: false) }
        }
        for spec in changeset.update + changeset.create {
            let reminder = EKReminder(eventStore: store)
            reminder.calendar = calendar
            reminder.title = spec.title
            reminder.notes = spec.notes ?? spec.tag
            if let due = spec.due, let date = Self.parse(due) {
                let hasTime = due.contains("T")
                let fields: Set<Calendar.Component> = hasTime
                    ? [.year, .month, .day, .hour, .minute]
                    : [.year, .month, .day]
                reminder.dueDateComponents = Calendar.current.dateComponents(fields, from: date)
            }
            try store.save(reminder, commit: false)
        }
        try store.commit()
    }

    // MARK: Internals

    private func agentActiveEventsByTagKey(on date: Date) -> [String: [EKEvent]] {
        let interval = Self.dayInterval(date)
        let predicate = store.predicateForEvents(
            withStart: interval.start, end: interval.end, calendars: nil)
        var map: [String: [EKEvent]] = [:]
        for event in store.events(matching: predicate) {
            if let key = AgentTag.value(in: event.notes, prefix: AgentTag.scheduledPrefix) {
                map[key, default: []].append(event)
            }
        }
        return map
    }

    private func fetchReminders() async -> [EKReminder] {
        await withCheckedContinuation { continuation in
            let predicate = store.predicateForReminders(in: nil)
            store.fetchReminders(matching: predicate) { reminders in
                // EventKit calls this on its own queue; we only touch these on the
                // caller afterwards. EKReminder isn't Sendable, so opt out of the
                // cross-isolation check explicitly.
                nonisolated(unsafe) let result = reminders ?? []
                continuation.resume(returning: result)
            }
        }
    }

    private func eventCalendar() throws -> EKCalendar {
        let title = "Schedule Agent"
        if let existing = store.calendars(for: .event)
            .first(where: { $0.title == title && $0.allowsContentModifications }) {
            return existing
        }
        let calendar = EKCalendar(for: .event, eventStore: store)
        calendar.title = title
        calendar.source = store.defaultCalendarForNewEvents?.source ?? store.sources.first
        try store.saveCalendar(calendar, commit: true)
        return calendar
    }

    private func reminderCalendar() throws -> EKCalendar {
        let title = "Schedule Agent"
        if let existing = store.calendars(for: .reminder)
            .first(where: { $0.title == title && $0.allowsContentModifications }) {
            return existing
        }
        let calendar = EKCalendar(for: .reminder, eventStore: store)
        calendar.title = title
        calendar.source = store.defaultCalendarForNewReminders()?.source ?? store.sources.first
        do {
            try store.saveCalendar(calendar, commit: true)
            return calendar
        } catch {
            if let fallback = store.defaultCalendarForNewReminders() { return fallback }
            throw error
        }
    }

    // MARK: Date helpers (backend emits "yyyy-MM-dd'T'HH:mm:ss" or "yyyy-MM-dd")

    private static let isoDateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    private static let isoDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func iso(_ date: Date) -> String { isoDateTime.string(from: date) }

    static func parse(_ value: String) -> Date? {
        if value.count >= 19, let d = isoDateTime.date(from: String(value.prefix(19))) { return d }
        if let d = isoDate.date(from: value) { return d }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: value) { return d }
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: value)
    }

    private static func dayInterval(_ date: Date) -> DateInterval {
        let start = Calendar.current.startOfDay(for: date)
        let end = Calendar.current.date(byAdding: .day, value: 1, to: start) ?? start.addingTimeInterval(86_400)
        return DateInterval(start: start, end: end)
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
