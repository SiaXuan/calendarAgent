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

    func generateSchedule(
        date: String, calendarEvents: [DayflowCalendarEventInput]? = nil
    ) async throws -> DayflowSchedule {
        // Local/EventKit path: when the frontend has read the day's real events,
        // upload them so the backend schedules around them without reading CalDAV.
        guard let calendarEvents else {
            return try await request("/schedule/generate", method: "POST", body: ["date": date])
        }
        let payload: [String: Any] = [
            "date": date,
            "calendar_events": calendarEvents.map { $0.jsonObject },
        ]
        let body = try JSONSerialization.data(withJSONObject: payload)
        return try await request("/schedule/generate", method: "POST", encodedBody: body)
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

// ─── Phase 4: projects, plan import, reminders + event change-sets ───────────
//
// See docs/ARCHITECTURE.md §0. The backend never touches the OS calendar; it
// returns change-sets (create/update/delete) that the EventKit layer applies,
// and turns projects/documents into schedulable Tasks + reminders. Dates that
// are date-only or datetime are kept as raw strings here — the EventKit layer
// parses them when it needs a `Date`.

// MARK: Inputs the frontend uploads (built from EventKit reads)

struct DayflowCalendarEventInput {
    var title: String?
    var start: String        // ISO datetime
    var end: String
    var description: String?  // event notes (carries agent tags)

    var jsonObject: [String: Any] {
        var o: [String: Any] = ["start": start, "end": end]
        if let title { o["title"] = title }
        if let description { o["description"] = description }
        return o
    }
}

struct DayflowCurrentEventInput {
    var tagKey: String
    var title: String?
    var start: String?
    var end: String?

    var jsonObject: [String: Any] {
        var o: [String: Any] = ["tag_key": tagKey]
        if let title { o["title"] = title }
        if let start { o["start"] = start }
        if let end { o["end"] = end }
        return o
    }
}

struct DayflowCurrentReminderInput {
    var tagKey: String
    var title: String?
    var due: String?

    var jsonObject: [String: Any] {
        var o: [String: Any] = ["tag_key": tagKey]
        if let title { o["title"] = title }
        if let due { o["due"] = due }
        return o
    }
}

// MARK: Responses

struct DayflowProject: Codable, Identifiable {
    var id: String
    var name: String
    var description: String?
    var source: String?
    var language: String?
    var status: String?
    var deadline: String?
    var startDate: String?
    var taskIDs: [String]?
    var notes: String?
    var createdAt: String?
    var updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, description, source, language, status, deadline, notes
        case startDate = "start_date"
        case taskIDs = "task_ids"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct DayflowDeleteResult: Codable {
    var projectID: String
    var deleted: Bool

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case deleted
    }
}

struct DayflowTagRef: Codable, Identifiable {
    var id: String { tagKey }
    var tagKey: String
    var tag: String

    enum CodingKeys: String, CodingKey {
        case tagKey = "tag_key"
        case tag
    }
}

struct DayflowReminderSpec: Codable, Identifiable {
    var id: String { tagKey }
    var blockKey: String
    var tagKey: String
    var tag: String
    var title: String
    var due: String?
    var notes: String?
    var projectID: String?

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case tagKey = "tag_key"
        case tag, title, due, notes
        case projectID = "project_id"
    }
}

struct DayflowReminderChangeset: Codable {
    var create: [DayflowReminderSpec]
    var update: [DayflowReminderSpec]
    var delete: [DayflowTagRef]
    var unchanged: Int
}

struct DayflowReplanResult: Codable {
    var projectID: String
    var reminders: DayflowReminderChangeset
    var affectedDates: [String]

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case reminders
        case affectedDates = "affected_dates"
    }
}

struct DayflowEventSpec: Codable, Identifiable {
    var id: String { tagKey }
    var blockKey: String
    var tagKey: String
    var tag: String
    var title: String
    var start: String
    var end: String
    var description: String?
    var notes: String?
    var projectID: String?

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case tagKey = "tag_key"
        case tag, title, start, end, description, notes
        case projectID = "project_id"
    }
}

struct DayflowEventChangeset: Codable {
    var create: [DayflowEventSpec]
    var update: [DayflowEventSpec]
    var delete: [DayflowTagRef]
    var unchanged: Int
}

struct DayflowProjectPlan: Codable {
    var projectID: String
    var items: [Item]

    struct Item: Codable, Identifiable {
        var id: String { blockKey }
        var blockKey: String
        var taskID: String
        var title: String
        var suggestedDate: String?
        var contentHash: String
        var status: String
        var done: Bool

        enum CodingKeys: String, CodingKey {
            case blockKey = "block_key"
            case taskID = "task_id"
            case title
            case suggestedDate = "suggested_date"
            case contentHash = "content_hash"
            case status, done
        }
    }

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case items
    }
}

struct DayflowProjectProgress: Codable {
    var projectID: String
    var total: Int
    var done: Int
    var byDay: [String: DayCount]

    struct DayCount: Codable {
        var total: Int
        var done: Int
    }

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case total, done
        case byDay = "by_day"
    }
}

struct DayflowImportedTask: Codable, Identifiable {
    var id: String
    var title: String
    var description: String?
    var priority: String?
    var estimatedHours: Double?
    var deadline: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, priority, deadline
        case estimatedHours = "estimated_hours"
    }
}

struct DayflowImportResult: Codable {
    var accepted: Bool
    var dryRun: Bool?
    var projectID: String
    var docKind: String?
    var confidence: Double?
    var reason: String?
    var tasks: [DayflowImportedTask]?

    enum CodingKeys: String, CodingKey {
        case accepted
        case dryRun = "dry_run"
        case projectID = "project_id"
        case docKind = "doc_kind"
        case confidence, reason, tasks
    }
}

struct DayflowHeatmap: Codable {
    var from: String
    var to: String
    var counts: [String: Int]
}

struct DayflowCompletionRecord: Codable, Identifiable {
    var id: String { blockKey }
    var blockKey: String
    var projectID: String?
    var taskID: String?
    var title: String
    var scheduledDate: String?
    var status: String
    var completedAt: String?

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case projectID = "project_id"
        case taskID = "task_id"
        case title
        case scheduledDate = "scheduled_date"
        case status
        case completedAt = "completed_at"
    }
}

struct DayflowCompletionsResponse: Codable {
    var completions: [DayflowCompletionRecord]
}

struct DayflowCompleteResult: Codable {
    var blockKey: String
    var done: Bool
    var foundBlock: Bool

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case done
        case foundBlock = "found_block"
    }
}

/// A non-2xx response that carries the body, so callers can show the server's
/// message (e.g. a plan-import rejection reason under FastAPI's `detail`).
struct DayflowRequestError: Error {
    let status: Int
    let body: Data

    /// Best-effort human message from common FastAPI error shapes.
    var message: String? {
        guard let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return String(data: body, encoding: .utf8)
        }
        if let detail = obj["detail"] as? String { return detail }
        if let detail = obj["detail"] as? [String: Any] {
            return (detail["reason"] as? String) ?? (detail["message"] as? String)
        }
        return nil
    }
}

extension DayflowAPIClient {

    // MARK: Project CRUD

    func listProjects() async throws -> [DayflowProject] {
        try await request("/projects", encodedBody: nil)
    }

    func fetchProject(id: String) async throws -> DayflowProject {
        try await request("/projects/\(id)", encodedBody: nil)
    }

    func createProject(
        name: String, description: String? = nil,
        deadline: String? = nil, startDate: String? = nil
    ) async throws -> DayflowProject {
        var body: [String: String] = ["name": name]
        if let description { body["description"] = description }
        if let deadline { body["deadline"] = deadline }
        if let startDate { body["start_date"] = startDate }
        return try await request("/projects", method: "POST", body: body)
    }

    func updateProject(
        id: String, name: String? = nil, status: String? = nil,
        deadline: String? = nil, startDate: String? = nil, notes: String? = nil
    ) async throws -> DayflowProject {
        var body: [String: String] = [:]
        if let name { body["name"] = name }
        if let status { body["status"] = status }
        if let deadline { body["deadline"] = deadline }
        if let startDate { body["start_date"] = startDate }
        if let notes { body["notes"] = notes }
        return try await request("/projects/\(id)", method: "PATCH", body: body)
    }

    func deleteProject(id: String) async throws -> DayflowDeleteResult {
        try await request("/projects/\(id)", method: "DELETE", encodedBody: nil)
    }

    // MARK: Plan / progress

    func fetchProjectPlan(id: String) async throws -> DayflowProjectPlan {
        try await request("/projects/\(id)/plan", encodedBody: nil)
    }

    func fetchProjectProgress(id: String) async throws -> DayflowProjectProgress {
        try await request("/projects/\(id)/progress", encodedBody: nil)
    }

    /// Completion-aware re-plan → a reminder change-set the EventKit layer applies.
    func replanProject(
        id: String, currentReminders: [DayflowCurrentReminderInput] = []
    ) async throws -> DayflowReplanResult {
        let payload: [String: Any] = [
            "current_reminders": currentReminders.map { $0.jsonObject }
        ]
        let body = try JSONSerialization.data(withJSONObject: payload)
        return try await request("/projects/\(id)/replan", method: "POST", encodedBody: body)
    }

    // MARK: Plan import (multipart: file XOR text)

    func importPlan(
        id: String, text: String, dryRun: Bool = false
    ) async throws -> DayflowImportResult {
        let boundary = "Boundary-\(UUID().uuidString)"
        let body = Self.multipartBody(
            boundary: boundary,
            fields: ["text": text, "dry_run": dryRun ? "true" : "false"],
            file: nil)
        return try await requestMultipart("/projects/\(id)/import", boundary: boundary, body: body)
    }

    func importPlan(
        id: String, fileURL: URL, dryRun: Bool = false
    ) async throws -> DayflowImportResult {
        let data = try Data(contentsOf: fileURL)
        let boundary = "Boundary-\(UUID().uuidString)"
        let body = Self.multipartBody(
            boundary: boundary,
            fields: ["dry_run": dryRun ? "true" : "false"],
            file: (field: "file", filename: fileURL.lastPathComponent,
                   mime: "application/octet-stream", data: data))
        return try await requestMultipart("/projects/\(id)/import", boundary: boundary, body: body)
    }

    // MARK: Completion tracking + dashboard

    func listCompletions(projectID: String? = nil) async throws -> DayflowCompletionsResponse {
        var path = "/completions"
        if let projectID { path += "?project_id=\(projectID)" }
        return try await request(path, encodedBody: nil)
    }

    func setBlockCompletion(
        date: String, blockKey: String, done: Bool
    ) async throws -> DayflowCompleteResult {
        // block_key is a {path} param containing "::" and spaces; appending(path:)
        // percent-encodes the segment for us.
        let path = "/schedule/\(date)/blocks/\(blockKey)/complete"
        return try await request(path, method: "POST", body: ["done": done ? "true" : "false"])
    }

    func fetchHeatmap(from: String? = nil, to: String? = nil) async throws -> DayflowHeatmap {
        var query: [String] = []
        if let from { query.append("from=\(from)") }
        if let to { query.append("to=\(to)") }
        let path = "/completions/heatmap" + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
        return try await request(path, encodedBody: nil)
    }

    // MARK: Event change-set (today's time blocks)

    func scheduleChangeset(
        date: String, currentEvents: [DayflowCurrentEventInput]
    ) async throws -> DayflowEventChangeset {
        let payload: [String: Any] = ["current_events": currentEvents.map { $0.jsonObject }]
        let body = try JSONSerialization.data(withJSONObject: payload)
        return try await request("/schedule/\(date)/changeset", method: "POST", encodedBody: body)
    }

    // MARK: Multipart helpers

    private func requestMultipart<T: Decodable>(
        _ path: String, boundary: String, body: Data
    ) async throws -> T {
        var req = URLRequest(url: baseURL.appending(path: path))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            // Surface the body so import rejections (422 with a reason) reach the UI.
            throw DayflowRequestError(status: (response as? HTTPURLResponse)?.statusCode ?? -1, body: data)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    private static func multipartBody(
        boundary: String,
        fields: [String: String],
        file: (field: String, filename: String, mime: String, data: Data)?
    ) -> Data {
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }
        for (key, value) in fields {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(key)\"\r\n\r\n")
            append("\(value)\r\n")
        }
        if let file {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(file.field)\"; filename=\"\(file.filename)\"\r\n")
            append("Content-Type: \(file.mime)\r\n\r\n")
            body.append(file.data)
            append("\r\n")
        }
        append("--\(boundary)--\r\n")
        return body
    }
}
