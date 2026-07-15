import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Task inbox")
struct TaskInboxTests {
    @Test("task inbox adds updates and removes tasks")
    func taskInboxMutatesTasks() throws {
        var inbox = TaskInbox()
        let task = TaskItem(title: "Plan next sprint")

        inbox.add(task)
        let inserted = try #require(inbox.tasks.first)
        #expect(inserted.title == "Plan next sprint")

        inbox.update(id: inserted.id) { item in
            item.estimatedMinutes = 45
            item.priority = .high
        }
        #expect(inbox.tasks.first?.estimatedMinutes == 45)
        #expect(inbox.tasks.first?.priority == .high)

        inbox.remove(id: inserted.id)
        #expect(inbox.tasks.isEmpty)
    }
}
