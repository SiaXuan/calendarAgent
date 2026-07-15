import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Calendar agenda")
struct CalendarAgendaTests {
    @Test("agenda sorts upcoming calendar events and excludes events that already ended")
    func agendaSortsUpcomingEvents() throws {
        let calendar = Calendar(identifier: .gregorian)
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9)))
        let earlier = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 8)))
        let later = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 15)))
        let soon = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 10)))

        let agenda = CalendarAgenda(events: [
            CalendarEvent(title: "Later", start: later, end: later.addingTimeInterval(30 * 60), isMovable: false, source: .appleCalendar),
            CalendarEvent(title: "Past", start: earlier, end: earlier.addingTimeInterval(30 * 60), isMovable: false, source: .appleCalendar),
            CalendarEvent(title: "Soon", start: soon, end: soon.addingTimeInterval(30 * 60), isMovable: false, source: .appleCalendar)
        ])

        #expect(agenda.upcoming(after: now).map(\.title) == ["Soon", "Later"])
    }

    @Test("agenda identifies the event currently in progress")
    func agendaFindsCurrentEvent() throws {
        let calendar = Calendar(identifier: .gregorian)
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9, minute: 30)))
        let currentStart = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9)))
        let currentEnd = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 10)))
        let later = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 11)))

        let agenda = CalendarAgenda(events: [
            CalendarEvent(title: "Current focus block", start: currentStart, end: currentEnd, isMovable: true, source: .agent),
            CalendarEvent(title: "Later review", start: later, end: later.addingTimeInterval(30 * 60), isMovable: false, source: .appleCalendar)
        ])

        #expect(agenda.current(at: now)?.title == "Current focus block")
    }
}
