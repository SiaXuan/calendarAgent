import Foundation
import ScheduleAgentCore

enum SampleData {
    static var tasks: [TaskItem] {
        let calendar = Calendar(identifier: .gregorian)
        let window = todayWindow()
        let endOfPlanningDay = window.end
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: endOfPlanningDay) ?? endOfPlanningDay.addingTimeInterval(24 * 60 * 60)

        return [
            TaskItem(
                title: "Design scheduling agent architecture",
                estimatedMinutes: 90,
                deadline: endOfPlanningDay,
                priority: .urgent,
                project: "Schedule Agent",
                isSplittable: false,
                energy: .deepWork
            ),
            TaskItem(
                title: "Review launch checklist",
                estimatedMinutes: 45,
                deadline: tomorrow,
                priority: .medium,
                project: "Launch",
                isSplittable: false,
                energy: .admin
            ),
            TaskItem(title: "Prepare investor update")
        ]
    }

    static var memories: [MemoryEntry] {
        [
            MemoryEntry(
                kind: .preference,
                text: "Deep work usually lands best before lunch.",
                signal: .preferDeepWorkInMorning
            ),
            MemoryEntry(
                kind: .personContext,
                text: "Meetings with external partners need buffer time.",
                signal: .addMeetingBuffer
            )
        ]
    }

    static var calendarEvents: [CalendarEvent] {
        let calendar = Calendar(identifier: .gregorian)
        let planningDay = todayWindow().start
        let start = calendar.date(bySettingHour: 10, minute: 0, second: 0, of: planningDay) ?? planningDay
        let end = calendar.date(bySettingHour: 11, minute: 0, second: 0, of: planningDay) ?? planningDay.addingTimeInterval(60 * 60)

        return [
            CalendarEvent(
                title: "Team sync",
                start: start,
                end: end,
                isMovable: false,
                source: .appleCalendar
            )
        ]
    }

    static func todayWindow() -> DateInterval {
        PlanningWindowFactory().window(containing: Date(), preferences: .standard)
    }
}
