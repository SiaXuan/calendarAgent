import Foundation
import ScheduleAgentCore

struct DayflowWriteResponse: Decodable {
    var written: Int
    var deleted: Int?
    var skipped: Bool?
}

struct DayflowPinResponse: Decodable {
    var blockKey: String
    var start: Date
    var durationMinutes: Int
    var adjusted: Bool
    var schedule: DayflowSchedule

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case start
        case durationMinutes = "duration_min"
        case adjusted
        case schedule
    }
}

struct DayflowAgentChatResult: Decodable {
    var terminalState: String
    var message: String
    var schedule: DayflowSchedule?
    var proposal: DayflowAgentProposal?

    enum CodingKeys: String, CodingKey {
        case terminalState = "terminal_state"
        case message
        case schedule
        case proposal
    }
}

struct DayflowAgentProposal: Decodable {
    var proposalID: String
    var summary: String
    var preview: DayflowSchedule
    var changes: [DayflowProposalChange]?

    enum CodingKeys: String, CodingKey {
        case proposalID = "proposal_id"
        case summary
        case preview
        case changes
    }
}

struct DayflowProposalChange: Decodable, Identifiable {
    var id: String { "\(scratchID)-\(op)-\(title)" }
    var op: String
    var scratchID: String
    var title: String
    var blockType: String
    var crossDay: Bool?
    var touchesSynced: Bool?
    var fromTime: String?
    var toTime: String?

    enum CodingKeys: String, CodingKey {
        case op
        case scratchID = "scratch_id"
        case title
        case blockType = "block_type"
        case crossDay = "cross_day"
        case touchesSynced = "touches_synced"
        case fromTime = "from_time"
        case toTime = "to_time"
    }
}

struct DayflowHealthSnapshot: Decodable {
    var date: String
    var sleep: DayflowSleepData
    var restingHeartRate: Int?
    var hrv: Double?
    var steps: Int?
    var activeMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case date
        case sleep
        case restingHeartRate = "resting_heart_rate"
        case hrv
        case steps
        case activeMinutes = "active_minutes"
    }
}

struct DayflowSleepData: Decodable {
    var durationHours: Double
    var sleepStart: Date
    var sleepEnd: Date

    enum CodingKeys: String, CodingKey {
        case durationHours = "duration_hours"
        case sleepStart = "sleep_start"
        case sleepEnd = "sleep_end"
    }
}

final class DayflowAPIClient: @unchecked Sendable {
    private let baseURL: URL
    private let session: URLSession

    init(baseURL: URL = URL(string: "http://localhost:8000")!, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    func generateSchedule(date: String) async throws -> DayflowSchedule {
        try await request("/schedule/generate", method: "POST", body: ["date": date])
    }

    func fetchSchedule(date: String) async throws -> DayflowSchedule {
        try await request("/schedule/\(date)", encodedBody: nil)
    }

    func fetchHealthSnapshot(date: String) async throws -> DayflowHealthSnapshot {
        try await request("/health/\(date)", encodedBody: nil)
    }

    func updateHealthSnapshot(
        date: String,
        sleepStart: Date,
        sleepEnd: Date,
        restingHeartRate: Int?,
        hrv: Double?,
        steps: Int?
    ) async throws -> DayflowHealthSnapshot {
        var body: [String: EncodableValue] = [
            "date": .string(date),
            "sleep_start": .string(Self.backendDateString(from: sleepStart)),
            "sleep_end": .string(Self.backendDateString(from: sleepEnd))
        ]
        if let restingHeartRate {
            body["resting_heart_rate"] = .int(restingHeartRate)
        }
        if let hrv {
            body["hrv"] = .double(hrv)
        }
        if let steps {
            body["steps"] = .int(steps)
        }
        return try await request("/health", method: "POST", body: body)
    }

    func streamSchedule(date: String) -> AsyncThrowingStream<DayflowStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: baseURL.appending(path: "/schedule/stream/\(date)"))
                    request.httpMethod = "GET"
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                        throw URLError(.badServerResponse)
                    }

                    for try await line in bytes.lines {
                        guard !Task.isCancelled else { break }
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line
                            .dropFirst("data:".count)
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !payload.isEmpty else { continue }

                        let event = try Self.decoder.decode(DayflowStreamEvent.self, from: Data(payload.utf8))
                        continuation.yield(event)
                        if case .done = event {
                            continuation.finish()
                            return
                        }
                        if case let .error(message) = event {
                            continuation.finish(throwing: URLError(.badServerResponse, userInfo: [
                                NSLocalizedDescriptionKey: message
                            ]))
                            return
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }

            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    func writeSchedule(date: String) async throws -> DayflowWriteResponse {
        try await request("/schedule/\(date)/write", method: "POST", encodedBody: nil)
    }

    func writeScheduleBlock(date: String, start: Date) async throws -> DayflowWriteResponse {
        try await request("/schedule/\(date)/blocks/write", method: "POST", body: ["start": Self.backendDateString(from: start)])
    }

    func pinBlock(date: String, blockKey: String, start: Date, durationMinutes: Int? = nil) async throws -> DayflowPinResponse {
        var body: [String: EncodableValue] = [
            "block_key": .string(blockKey),
            "start_iso": .string(Self.backendDateString(from: start))
        ]
        if let durationMinutes {
            body["duration_min"] = .int(durationMinutes)
        }
        return try await request("/schedule/\(date)/pin", method: "POST", body: body)
    }

    func sendAgentMessage(date: String, message: String) async throws -> DayflowAgentChatResult {
        try await request("/chat/agent", method: "POST", body: ["date": date, "message": message])
    }

    func confirmAgentProposal(date: String) async throws -> DayflowAgentChatResult {
        try await request("/chat/agent/confirm", method: "POST", body: ["date": date])
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: String]? = nil) async throws -> T {
        let encodedBody = try body.map { try JSONEncoder().encode($0) }
        return try await request(path, method: method, encodedBody: encodedBody)
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: EncodableValue]) async throws -> T {
        let payload = Dictionary(uniqueKeysWithValues: body.map { key, value in
            (key, value.jsonValue)
        })
        let encodedBody = try JSONSerialization.data(withJSONObject: payload)
        return try await request(path, method: method, encodedBody: encodedBody)
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", encodedBody: Data? = nil) async throws -> T {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = encodedBody

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    private static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            if let parsed = parseBackendDate(value) {
                return parsed
            }
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath, debugDescription: "Invalid backend date: \(value)")
            )
        }
        return decoder
    }

    private static func backendDateString(from date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter.string(from: date)
    }

    private static func parseBackendDate(_ value: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) {
            return date
        }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: value) {
            return date
        }

        let localDateTime = DateFormatter()
        localDateTime.locale = Locale(identifier: "en_US_POSIX")
        localDateTime.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let date = localDateTime.date(from: value) {
            return date
        }

        let day = DateFormatter()
        day.locale = Locale(identifier: "en_US_POSIX")
        day.dateFormat = "yyyy-MM-dd"
        return day.date(from: value)
    }
}

private enum EncodableValue {
    case string(String)
    case int(Int)
    case double(Double)

    var jsonValue: Any {
        switch self {
        case let .string(value):
            value
        case let .int(value):
            value
        case let .double(value):
            value
        }
    }
}
