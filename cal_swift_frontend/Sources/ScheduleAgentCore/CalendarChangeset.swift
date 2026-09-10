import Foundation

/// The change-set the backend returns for a day's schedule, and the pure
/// decision of what applying it does to the calendar.
///
/// These types live in Core rather than next to the API client so they can be
/// tested: the executable target isn't importable from tests, and
/// `applyEventChangeset` — the code that actually writes to your calendar — had
/// no coverage at all. Splitting the decision (here, pure) from the execution
/// (EventKit calls in `AppleCalendarAdapter`) means the part with the logic in
/// it can be tested without a calendar database or TCC permission.

// MARK: - Wire types

public struct DayflowTagRef: Codable, Identifiable, Equatable, Sendable {
    public var id: String { tagKey }
    public var tagKey: String
    public var tag: String

    public init(tagKey: String, tag: String) {
        self.tagKey = tagKey
        self.tag = tag
    }

    enum CodingKeys: String, CodingKey {
        case tagKey = "tag_key"
        case tag
    }
}

public struct DayflowEventSpec: Codable, Identifiable, Equatable, Sendable {
    public var id: String { tagKey }
    public var blockKey: String
    public var tagKey: String
    public var tag: String
    public var title: String
    public var start: String
    public var end: String
    public var description: String?
    public var notes: String?
    public var projectID: String?

    public init(
        blockKey: String, tagKey: String, tag: String, title: String,
        start: String, end: String, description: String? = nil,
        notes: String? = nil, projectID: String? = nil
    ) {
        self.blockKey = blockKey
        self.tagKey = tagKey
        self.tag = tag
        self.title = title
        self.start = start
        self.end = end
        self.description = description
        self.notes = notes
        self.projectID = projectID
    }

    enum CodingKeys: String, CodingKey {
        case blockKey = "block_key"
        case tagKey = "tag_key"
        case tag, title, start, end, description, notes
        case projectID = "project_id"
    }
}

public struct DayflowEventChangeset: Codable, Equatable, Sendable {
    public var create: [DayflowEventSpec]
    public var update: [DayflowEventSpec]
    public var delete: [DayflowTagRef]
    public var unchanged: Int

    public init(
        create: [DayflowEventSpec] = [], update: [DayflowEventSpec] = [],
        delete: [DayflowTagRef] = [], unchanged: Int = 0
    ) {
        self.create = create
        self.update = update
        self.delete = delete
        self.unchanged = unchanged
    }
}

// MARK: - The plan

/// One event the plan says to write, with its timestamps already resolved.
public struct PlannedCalendarEvent: Equatable, Sendable {
    public let tagKey: String
    public let title: String
    public let start: Date
    public let end: Date
    public let notes: String

    public init(tagKey: String, title: String, start: Date, end: Date, notes: String) {
        self.tagKey = tagKey
        self.title = title
        self.start = start
        self.end = end
        self.notes = notes
    }
}

/// What applying a change-set does, as data rather than as side effects.
public struct CalendarChangePlan: Equatable, Sendable {
    /// Tag keys whose existing events get deleted. Only real deletes — an
    /// update no longer removes anything.
    public var tagKeysToRemove: [String]
    /// Events that already exist and should be edited in place, matched by tag
    /// key. If no event carries the tag, the executor writes it as new.
    public var eventsToUpdate: [PlannedCalendarEvent]
    /// Events to write fresh.
    public var eventsToCreate: [PlannedCalendarEvent]
    /// Specs skipped because their timestamps couldn't be parsed. Surfaced
    /// rather than silently dropped so a malformed payload is diagnosable.
    public var unparsableTagKeys: [String]

    public init(
        tagKeysToRemove: [String] = [],
        eventsToUpdate: [PlannedCalendarEvent] = [],
        eventsToCreate: [PlannedCalendarEvent] = [],
        unparsableTagKeys: [String] = []
    ) {
        self.tagKeysToRemove = tagKeysToRemove
        self.eventsToUpdate = eventsToUpdate
        self.eventsToCreate = eventsToCreate
        self.unparsableTagKeys = unparsableTagKeys
    }

    /// Decide what a change-set means, without touching EventKit.
    ///
    /// An update edits the existing event rather than deleting and recreating
    /// it. Remove-then-recreate was inherited from CalDAV, which has no PATCH,
    /// and it cost real things under EventKit: recreating changes the event's
    /// identity, so any notes, attachments or custom alerts the user added are
    /// lost, and other devices see a delete plus an add instead of an edit.
    /// It also meant a spec whose timestamps failed to parse deleted the old
    /// event and wrote nothing — a malformed response could clear the day.
    public static func plan(for changeset: DayflowEventChangeset) -> CalendarChangePlan {
        var plan = CalendarChangePlan()
        plan.tagKeysToRemove = changeset.delete.map(\.tagKey)

        func planned(_ spec: DayflowEventSpec) -> PlannedCalendarEvent? {
            guard let start = CalendarDateFormat.parse(spec.start),
                  let end = CalendarDateFormat.parse(spec.end) else {
                plan.unparsableTagKeys.append(spec.tagKey)
                return nil
            }
            return PlannedCalendarEvent(
                tagKey: spec.tagKey,
                title: spec.title,
                start: start,
                end: end,
                // The tag is the fallback because it's what identifies the event
                // as ours on the next diff — an event written without it would
                // be invisible to reconcile and get recreated every sync.
                notes: spec.notes ?? spec.tag
            )
        }

        plan.eventsToUpdate = changeset.update.compactMap(planned)
        plan.eventsToCreate = changeset.create.compactMap(planned)
        return plan
    }
}

// MARK: - Date parsing

public enum CalendarDateFormat {
    private static let isoDateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    private static let isoDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    public static func iso(_ date: Date) -> String { isoDateTime.string(from: date) }
    public static func dayKey(_ date: Date) -> String { isoDate.string(from: date) }

    /// Backend datetimes are local-timezone and unsuffixed ("2026-06-15T09:00:00"),
    /// so the plain formatter is tried first; the ISO8601 fallbacks cover values
    /// that arrive with an offset or fractional seconds.
    public static func parse(_ value: String) -> Date? {
        if value.count >= 19, let d = isoDateTime.date(from: String(value.prefix(19))) { return d }
        if let d = isoDate.date(from: value) { return d }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: value) { return d }
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: value)
    }
}
