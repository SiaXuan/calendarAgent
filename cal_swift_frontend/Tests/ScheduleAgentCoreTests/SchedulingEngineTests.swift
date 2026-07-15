import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Scheduling engine")
struct SchedulingEngineTests {
    @Test("schedules tasks before deadlines without overlapping existing calendar events")
    func schedulesTasksAroundBusyEvents() throws {
        let calendar = Calendar(identifier: .gregorian)
        let day = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9)))
        let end = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 17)))
        let meetingStart = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 10)))
        let meetingEnd = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 11)))
        let deadline = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 16)))

        let task = TaskItem(
            title: "Draft launch memo",
            estimatedMinutes: 90,
            deadline: deadline,
            priority: .high,
            project: "Launch",
            isSplittable: false,
            energy: .deepWork
        )
        let event = CalendarEvent(
            title: "Team sync",
            start: meetingStart,
            end: meetingEnd,
            isMovable: false,
            source: .appleCalendar
        )
        let preferences = UserPreferences(
            workdayStartHour: 9,
            workdayEndHour: 17,
            minimumBlockMinutes: 30,
            bufferMinutes: 10,
            deepWorkPreferredHours: 9..<12
        )

        let plan = SchedulingEngine().proposeSchedule(
            tasks: [task],
            events: [event],
            memories: [],
            preferences: preferences,
            planningWindow: DateInterval(start: day, end: end)
        )

        #expect(plan.scheduledBlocks.count == 1)
        let block = try #require(plan.scheduledBlocks.first)
        #expect(block.taskTitle == "Draft launch memo")
        #expect(block.start >= day)
        #expect(block.end <= deadline)
        #expect(block.end <= meetingStart || block.start >= meetingEnd)
        #expect(plan.risks.isEmpty)
    }

    @Test("asks for missing task details before scheduling incomplete tasks")
    func asksForMissingTaskDetails() throws {
        let task = TaskItem(title: "Prepare investor update")
        let plan = SchedulingEngine().proposeSchedule(
            tasks: [task],
            events: [],
            memories: [],
            preferences: .standard,
            planningWindow: DateInterval(start: .now, duration: 8 * 60 * 60)
        )

        #expect(plan.scheduledBlocks.isEmpty)
        #expect(plan.clarifyingQuestions.contains("How long should I reserve for Prepare investor update?"))
        #expect(plan.clarifyingQuestions.contains("When is Prepare investor update due?"))
    }

    @Test("uses personal memory to prefer deep work in the morning")
    func usesMemoryToPreferDeepWork() throws {
        let calendar = Calendar(identifier: .gregorian)
        let start = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9)))
        let end = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 17)))
        let deadline = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 17)))
        let task = TaskItem(
            title: "Design architecture",
            estimatedMinutes: 60,
            deadline: deadline,
            priority: .medium,
            project: "Agent",
            isSplittable: false,
            energy: .deepWork
        )
        let memory = MemoryEntry(
            kind: .preference,
            text: "User repeatedly moves deep work to the morning.",
            signal: .preferDeepWorkInMorning
        )

        let plan = SchedulingEngine().proposeSchedule(
            tasks: [task],
            events: [],
            memories: [memory],
            preferences: .standard,
            planningWindow: DateInterval(start: start, end: end)
        )

        let block = try #require(plan.scheduledBlocks.first)
        let hour = calendar.component(.hour, from: block.start)
        #expect(hour < 12)
        #expect(plan.explanations.contains { $0.contains("morning deep work preference") })
    }

    @Test("reports risk when deadline cannot be met")
    func reportsDeadlineRisk() throws {
        let calendar = Calendar(identifier: .gregorian)
        let start = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 9)))
        let deadline = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 10)))
        let end = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 17)))
        let task = TaskItem(
            title: "Finish proposal",
            estimatedMinutes: 120,
            deadline: deadline,
            priority: .urgent,
            project: "Sales",
            isSplittable: false,
            energy: .deepWork
        )

        let plan = SchedulingEngine().proposeSchedule(
            tasks: [task],
            events: [],
            memories: [],
            preferences: .standard,
            planningWindow: DateInterval(start: start, end: end)
        )

        #expect(plan.scheduledBlocks.isEmpty)
        #expect(plan.risks.contains { $0.taskTitle == "Finish proposal" && $0.reason.contains("before its deadline") })
    }

    @Test("calendar writer refuses to write unconfirmed plans")
    func writerRequiresConfirmation() throws {
        let writer = InMemoryCalendarWriter()
        let plan = SchedulePlan(
            scheduledBlocks: [
                ScheduledBlock(
                    taskID: UUID(),
                    taskTitle: "Review roadmap",
                    start: .now,
                    end: .now.addingTimeInterval(30 * 60),
                    rationale: "Fits before lunch."
                )
            ],
            unscheduledTasks: [],
            clarifyingQuestions: [],
            risks: [],
            explanations: []
        )

        #expect(throws: CalendarWriteError.planNotConfirmed) {
            try writer.write(plan: plan, confirmation: nil)
        }
        try writer.write(plan: plan, confirmation: PlanConfirmation(acceptedBlockIDs: Set(plan.scheduledBlocks.map(\.id))))
        #expect(writer.writtenBlocks.count == 1)
    }
}
