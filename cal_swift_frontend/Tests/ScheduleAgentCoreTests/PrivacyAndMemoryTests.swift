import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Privacy and memory")
struct PrivacyAndMemoryTests {
    @Test("memory vault supports viewing editing and deleting personal context")
    func memoryVaultSupportsUserControl() throws {
        var vault = MemoryVault(memories: [
            MemoryEntry(kind: .personContext, text: "Alex prefers afternoon reviews.", signal: .generic)
        ])

        let original = try #require(vault.all.first)
        vault.update(id: original.id, text: "Alex prefers short afternoon reviews.", signal: .addMeetingBuffer)
        #expect(vault.all.first?.text == "Alex prefers short afternoon reviews.")
        #expect(vault.all.first?.signal == .addMeetingBuffer)

        vault.delete(id: original.id)
        #expect(vault.all.isEmpty)
    }

    @Test("cloud LLM context summarizes calendar without exposing event titles")
    func cloudContextMinimizesCalendarDetails() throws {
        let calendar = Calendar(identifier: .gregorian)
        let start = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 13)))
        let end = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 23, hour: 14)))
        let events = [
            CalendarEvent(
                title: "Therapy appointment",
                start: start,
                end: end,
                isMovable: false,
                source: .appleCalendar
            )
        ]

        let context = PrivacyContextBuilder().build(
            tasks: [TaskItem(title: "Write strategy memo", estimatedMinutes: 60, deadline: end)],
            events: events,
            memories: [MemoryEntry(kind: .preference, text: "Deep work before lunch.", signal: .preferDeepWorkInMorning)]
        )

        #expect(context.calendarSummary.contains("1 busy block"))
        #expect(!context.calendarSummary.contains("Therapy"))
        #expect(context.tasks.first?.title == "Write strategy memo")
        #expect(context.memories.first?.text == "Deep work before lunch.")
    }
}
