import CoreGraphics
import Foundation

public enum ScheduleDragTargetResolver {
    public static func targetTaskID(
        forY y: CGFloat,
        draggedTaskID: UUID,
        frames: [UUID: CGRect]
    ) -> UUID? {
        let candidates = frames.filter { id, _ in id != draggedTaskID }
        if let containing = candidates.first(where: { _, frame in
            frame.minY <= y && y <= frame.maxY
        }) {
            return containing.key
        }

        let nearest = candidates.min { lhs, rhs in
            abs(lhs.value.midY - y) < abs(rhs.value.midY - y)
        }
        guard let nearest, abs(nearest.value.midY - y) <= nearest.value.height else {
            return nil
        }
        return nearest.key
    }
}

public struct ScheduleTimelineDropMapper: Sendable {
    public var targetDate: String
    public var workStartHour: Int
    public var workEndHour: Int
    public var snapMinutes: Int
    public var calendar: Calendar

    public init(
        targetDate: String,
        workStartHour: Int,
        workEndHour: Int,
        snapMinutes: Int = 15,
        calendar: Calendar = .current
    ) {
        self.targetDate = targetDate
        self.workStartHour = workStartHour
        self.workEndHour = workEndHour
        self.snapMinutes = snapMinutes
        self.calendar = calendar
    }

    public func date(forY y: CGFloat, inHeight height: CGFloat) -> Date? {
        guard height > 0,
              workEndHour > workStartHour,
              snapMinutes > 0,
              let day = dayStart()
        else {
            return nil
        }

        let clampedY = min(max(y, 0), height)
        let ratio = Double(clampedY / height)
        let totalMinutes = Double((workEndHour - workStartHour) * 60)
        let rawMinutes = totalMinutes * ratio
        let snappedMinutes = Int((rawMinutes / Double(snapMinutes)).rounded()) * snapMinutes
        let boundedMinutes = min(max(snappedMinutes, 0), Int(totalMinutes))

        guard let start = calendar.date(bySettingHour: workStartHour, minute: 0, second: 0, of: day) else {
            return nil
        }
        return calendar.date(byAdding: .minute, value: boundedMinutes, to: start)
    }

    private func dayStart() -> Date? {
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
