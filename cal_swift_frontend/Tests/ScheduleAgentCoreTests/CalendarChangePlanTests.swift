import Testing
import Foundation
@testable import ScheduleAgentCore

/// Coverage for what a change-set does to the calendar.
///
/// `applyEventChangeset` — the code that actually writes to the user's calendar
/// — had no tests at all. Its EventKit calls still can't be unit-tested without
/// a calendar database, but the decision they execute now can be.
@Suite("Calendar change plan")
struct CalendarChangePlanTests {

    private func spec(
        _ tagKey: String, title: String = "Write",
        start: String = "2026-06-15T09:00:00",
        end: String = "2026-06-15T10:00:00",
        notes: String? = nil
    ) -> DayflowEventSpec {
        DayflowEventSpec(
            blockKey: "t1::\(title)", tagKey: tagKey, tag: "[agent-scheduled:dayflow:\(tagKey)]",
            title: title, start: start, end: end, notes: notes)
    }

    private func date(_ hour: Int, _ minute: Int = 0) -> Date {
        var c = DateComponents()
        c.year = 2026; c.month = 6; c.day = 15; c.hour = hour; c.minute = minute
        return Calendar.current.date(from: c)!
    }

    @Test("a create writes one event and removes nothing")
    func createOnly() {
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(create: [spec("aaa")]))

        #expect(plan.tagKeysToRemove.isEmpty)
        #expect(plan.eventsToCreate.count == 1)
        #expect(plan.eventsToCreate[0].tagKey == "aaa")
        #expect(plan.eventsToCreate[0].start == date(9))
        #expect(plan.eventsToCreate[0].end == date(10))
    }

    @Test("a delete removes its tag and writes nothing")
    func deleteOnly() {
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(
            delete: [DayflowTagRef(tagKey: "bbb", tag: "[agent-scheduled:dayflow:bbb]")]))

        #expect(plan.tagKeysToRemove == ["bbb"])
        #expect(plan.eventsToCreate.isEmpty)
    }

    @Test("an update edits in place and deletes nothing")
    func updateDoesNotDelete() {
        // Remove-then-recreate was a CalDAV habit (no PATCH). Under EventKit it
        // changed the event's identity, losing the user's own notes, attachments
        // and alerts, and syncing to other devices as delete + add.
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(
            update: [spec("ccc", start: "2026-06-15T14:00:00", end: "2026-06-15T15:00:00")]))

        #expect(plan.tagKeysToRemove.isEmpty)
        #expect(plan.eventsToUpdate.map(\.tagKey) == ["ccc"])
        #expect(plan.eventsToUpdate[0].start == date(14))
        #expect(plan.eventsToCreate.isEmpty)
    }

    @Test("the three kinds stay in their own lanes")
    func kindsDoNotMix() {
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(
            create: [spec("new")],
            update: [spec("old")],
            delete: [DayflowTagRef(tagKey: "gone", tag: "t")]))

        #expect(plan.tagKeysToRemove == ["gone"])
        #expect(plan.eventsToUpdate.map(\.tagKey) == ["old"])
        #expect(plan.eventsToCreate.map(\.tagKey) == ["new"])
    }

    @Test("unchanged blocks produce no work at all")
    func unchangedIsANoOp() {
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(unchanged: 7))

        #expect(plan.tagKeysToRemove.isEmpty)
        #expect(plan.eventsToUpdate.isEmpty)
        #expect(plan.eventsToCreate.isEmpty)
        #expect(plan.unparsableTagKeys.isEmpty)
    }

    @Test("the agent tag is the fallback when notes are absent")
    func tagIsTheNotesFallback() {
        // An event written without the tag is invisible to the next reconcile,
        // so it would be recreated on every sync.
        let untagged = CalendarChangePlan.plan(for: DayflowEventChangeset(create: [spec("ddd")]))
        #expect(untagged.eventsToCreate[0].notes == "[agent-scheduled:dayflow:ddd]")

        let withNotes = CalendarChangePlan.plan(
            for: DayflowEventChangeset(create: [spec("eee", notes: "context [agent-scheduled:dayflow:eee]")]))
        #expect(withNotes.eventsToCreate[0].notes == "context [agent-scheduled:dayflow:eee]")
    }

    @Test("an unparsable timestamp is reported, not silently dropped")
    func unparsableSpecIsSurfaced() {
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(
            create: [spec("good"), spec("bad", start: "not-a-date")]))

        #expect(plan.eventsToCreate.map(\.tagKey) == ["good"])
        #expect(plan.unparsableTagKeys == ["bad"])
    }

    @Test("a bad update leaves the existing event alone")
    func unparsableUpdateTouchesNothing() {
        // Under remove-then-recreate this deleted the old event and wrote
        // nothing, so a malformed backend response could clear the day. Now an
        // unusable update is simply skipped and the calendar keeps what it had.
        let plan = CalendarChangePlan.plan(for: DayflowEventChangeset(
            update: [spec("fff", end: "garbage")]))

        #expect(plan.tagKeysToRemove.isEmpty)
        #expect(plan.eventsToUpdate.isEmpty)
        #expect(plan.eventsToCreate.isEmpty)
        #expect(plan.unparsableTagKeys == ["fff"])
    }
}

@Suite("Calendar date parsing")
struct CalendarDateFormatTests {

    @Test("parses the backend's local unsuffixed datetime")
    func parsesLocalDateTime() throws {
        let d = try #require(CalendarDateFormat.parse("2026-06-15T09:30:00"))
        let c = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: d)
        #expect(c.hour == 9 && c.minute == 30 && c.day == 15)
    }

    @Test("parses a bare date")
    func parsesDateOnly() throws {
        let d = try #require(CalendarDateFormat.parse("2026-06-15"))
        #expect(Calendar.current.component(.day, from: d) == 15)
    }

    @Test("parses ISO8601 with offset and with fractional seconds")
    func parsesISOVariants() {
        #expect(CalendarDateFormat.parse("2026-06-15T09:00:00+02:00") != nil)
        #expect(CalendarDateFormat.parse("2026-06-15T09:00:00.123Z") != nil)
    }

    @Test("rejects junk instead of guessing")
    func rejectsJunk() {
        #expect(CalendarDateFormat.parse("not-a-date") == nil)
        #expect(CalendarDateFormat.parse("") == nil)
    }

    @Test("round-trips through iso()")
    func roundTrip() throws {
        let original = try #require(CalendarDateFormat.parse("2026-06-15T09:00:00"))
        #expect(CalendarDateFormat.iso(original) == "2026-06-15T09:00:00")
        #expect(CalendarDateFormat.dayKey(original) == "2026-06-15")
    }
}
