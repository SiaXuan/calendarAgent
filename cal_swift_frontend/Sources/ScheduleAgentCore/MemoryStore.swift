import Foundation

public protocol MemoryStoring {
    func load() throws -> [MemoryEntry]
    func save(_ memories: [MemoryEntry]) throws
}

public struct JSONMemoryStore: MemoryStoring {
    public var fileURL: URL

    public init(fileURL: URL) {
        self.fileURL = fileURL
    }

    public func load() throws -> [MemoryEntry] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return []
        }
        let data = try Data(contentsOf: fileURL)
        return try JSONDecoder().decode([MemoryEntry].self, from: data)
    }

    public func save(_ memories: [MemoryEntry]) throws {
        let folder = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let data = try JSONEncoder.prettyAgentEncoder.encode(memories)
        try data.write(to: fileURL, options: .atomic)
    }
}

private extension JSONEncoder {
    static var prettyAgentEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}
