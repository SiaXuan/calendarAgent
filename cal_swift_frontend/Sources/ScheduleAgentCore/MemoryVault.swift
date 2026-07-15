import Foundation

public struct MemoryVault: Sendable {
    private var memories: [MemoryEntry]

    public init(memories: [MemoryEntry] = []) {
        self.memories = memories
    }

    public var all: [MemoryEntry] {
        memories.sorted { $0.createdAt < $1.createdAt }
    }

    public mutating func add(_ memory: MemoryEntry) {
        memories.append(memory)
    }

    public mutating func update(id: UUID, text: String, signal: MemorySignal) {
        guard let index = memories.firstIndex(where: { $0.id == id }) else {
            return
        }
        memories[index].text = text
        memories[index].signal = signal
    }

    public mutating func delete(id: UUID) {
        memories.removeAll { $0.id == id }
    }
}
