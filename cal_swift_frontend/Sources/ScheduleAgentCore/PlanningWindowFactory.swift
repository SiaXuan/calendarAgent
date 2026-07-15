import Foundation

public struct PlanningWindowFactory: Sendable {
    private let calendar: Calendar

    public init(calendar: Calendar = Calendar(identifier: .gregorian)) {
        self.calendar = calendar
    }

    public func window(containing now: Date = .now, preferences: UserPreferences) -> DateInterval {
        let todayStart = workdayStart(on: now, preferences: preferences)
        let todayEnd = workdayEnd(on: now, preferences: preferences)

        if now < todayStart {
            return DateInterval(start: todayStart, end: todayEnd)
        }

        if now < todayEnd {
            return DateInterval(start: now, end: todayEnd)
        }

        let nextDay = calendar.date(byAdding: .day, value: 1, to: now) ?? now.addingTimeInterval(24 * 60 * 60)
        let nextStart = workdayStart(on: nextDay, preferences: preferences)
        let nextEnd = workdayEnd(on: nextDay, preferences: preferences)
        return DateInterval(start: nextStart, end: nextEnd)
    }

    private func workdayStart(on date: Date, preferences: UserPreferences) -> Date {
        calendar.date(
            bySettingHour: preferences.workdayStartHour,
            minute: 0,
            second: 0,
            of: date
        ) ?? date
    }

    private func workdayEnd(on date: Date, preferences: UserPreferences) -> Date {
        calendar.date(
            bySettingHour: preferences.workdayEndHour,
            minute: 0,
            second: 0,
            of: date
        ) ?? date.addingTimeInterval(8 * 60 * 60)
    }
}
