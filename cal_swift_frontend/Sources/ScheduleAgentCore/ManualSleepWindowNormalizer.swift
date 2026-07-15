import Foundation

public enum ManualSleepWindowNormalizer {
    public static func normalized(
        sleepStart: Date,
        sleepEnd: Date,
        targetDate: String,
        calendar: Calendar = .current
    ) -> (start: Date, end: Date)? {
        let targetDay = day(from: targetDate, calendar: calendar)
        guard let targetDay else { return nil }

        let startComponents = calendar.dateComponents([.hour, .minute], from: sleepStart)
        let endComponents = calendar.dateComponents([.hour, .minute], from: sleepEnd)
        guard let startHour = startComponents.hour,
              let startMinute = startComponents.minute,
              let endHour = endComponents.hour,
              let endMinute = endComponents.minute
        else {
            return nil
        }

        let startsBeforeWake = startHour < endHour || (startHour == endHour && startMinute < endMinute)
        let startDay = startsBeforeWake
            ? targetDay
            : calendar.date(byAdding: .day, value: -1, to: targetDay)
        guard let startDay,
              let normalizedStart = calendar.date(bySettingHour: startHour, minute: startMinute, second: 0, of: startDay),
              let normalizedEnd = calendar.date(bySettingHour: endHour, minute: endMinute, second: 0, of: targetDay)
        else {
            return nil
        }

        return (normalizedStart, normalizedEnd)
    }

    private static func day(from targetDate: String, calendar: Calendar) -> Date? {
        let parts = targetDate.split(separator: "-")
        guard parts.count == 3,
              let year = Int(parts[0]),
              let month = Int(parts[1]),
              let day = Int(parts[2])
        else {
            return nil
        }
        return calendar.date(from: DateComponents(year: year, month: month, day: day))
    }
}
