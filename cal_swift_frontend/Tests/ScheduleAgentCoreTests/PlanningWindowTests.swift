import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Planning window")
struct PlanningWindowTests {
    @Test("moves planning window to next workday after work hours")
    func movesWindowAfterWorkHours() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = try #require(calendar.date(from: DateComponents(timeZone: calendar.timeZone, year: 2026, month: 6, day: 23, hour: 20, minute: 30)))

        let window = PlanningWindowFactory(calendar: calendar).window(
            containing: now,
            preferences: .standard
        )

        #expect(calendar.component(.day, from: window.start) == 24)
        #expect(calendar.component(.hour, from: window.start) == 9)
        #expect(calendar.component(.hour, from: window.end) == 17)
        #expect(window.start < window.end)
    }

    @Test("starts planning window at now during work hours")
    func startsAtNowDuringWorkHours() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = try #require(calendar.date(from: DateComponents(timeZone: calendar.timeZone, year: 2026, month: 6, day: 23, hour: 11, minute: 15)))

        let window = PlanningWindowFactory(calendar: calendar).window(
            containing: now,
            preferences: .standard
        )

        #expect(window.start == now)
        #expect(calendar.component(.hour, from: window.end) == 17)
        #expect(window.start < window.end)
    }
}
