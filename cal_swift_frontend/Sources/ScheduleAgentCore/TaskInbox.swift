import Foundation

public struct TaskInbox: Sendable {
    private var storage: [TaskItem]

    public init(tasks: [TaskItem] = []) {
        self.storage = tasks
    }

    public var tasks: [TaskItem] {
        storage
    }

    public mutating func add(_ task: TaskItem) {
        storage.append(task)
    }

    public mutating func update(id: UUID, mutate: (inout TaskItem) -> Void) {
        guard let index = storage.firstIndex(where: { $0.id == id }) else {
            return
        }
        mutate(&storage[index])
    }

    public mutating func remove(id: UUID) {
        storage.removeAll { $0.id == id }
    }
}
