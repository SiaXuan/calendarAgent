import Foundation

/// Manual on-device check for the Phase 4 EventKit executor. Run with:
///
///     swift run ScheduleAgentApp --verify-eventkit
///
/// It talks ONLY to the local Calendar/Reminders via AppleCalendarAdapter — no
/// backend — building change-sets by hand and applying them, so a green run
/// proves the executor create/read/delete round-trip works. Requires the
/// running process (Terminal) to have Calendar + Reminders access in
/// System Settings › Privacy & Security.
enum EventKitVerification {

    static func run() {
        let semaphore = DispatchSemaphore(value: 0)
        Task.detached {
            await performVerification()
            semaphore.signal()
        }
        semaphore.wait()
    }

    private static func performVerification() async {
        let adapter = AppleCalendarAdapter()

        print("1) Requesting Calendar + Reminders access…")
        let (granted, error) = await adapter.requestFullAccess()
        guard granted else {
            print("   ❌ access NOT granted: \(error?.localizedDescription ?? "denied").")
            print("      Grant Terminal (or your runner) Calendar AND Reminders access in")
            print("      System Settings › Privacy & Security, then re-run.")
            return
        }
        print("   ✅ access granted")

        let today = Date()
        await verifyEvents(adapter: adapter, today: today)
        await verifyReminders(adapter: adapter, today: today)

        print("")
        print("Done. If steps 2 & 5 showed the item appear in Calendar/Reminders and")
        print("steps 4 & 7 reported 0 afterwards, the EventKit executor works.")
    }

    // MARK: Event round-trip

    private static func verifyEvents(adapter: AppleCalendarAdapter, today: Date) async {
        let hash = "verifyevt01"
        let tag = "[agent-scheduled:dayflow:\(hash)]"
        let spec = DayflowEventSpec(
            blockKey: "verify::Focus", tagKey: hash, tag: tag,
            title: "Verify Focus Block",
            start: AppleCalendarAdapter.iso(at(9, today)),
            end: AppleCalendarAdapter.iso(at(10, today)),
            description: "EventKit verify",
            notes: "EventKit verify\n\(tag)", projectID: nil)

        do {
            print("2) Creating a test event via applyEventChangeset…")
            try adapter.applyEventChangeset(
                DayflowEventChangeset(create: [spec], update: [], delete: [], unchanged: 0),
                on: today)
            let readBack = adapter.currentAgentEvents(on: today)
            print("3) currentAgentEvents read back \(readBack.count): \(readBack.map { $0.title ?? "?" })")
            print("   👉 check Calendar.app — “Verify Focus Block” at 09:00 today, in a “Schedule Agent” calendar")

            print("4) Deleting it via a delete change-set…")
            try adapter.applyEventChangeset(
                DayflowEventChangeset(
                    create: [], update: [],
                    delete: [DayflowTagRef(tagKey: hash, tag: tag)], unchanged: 0),
                on: today)
            let after = adapter.currentAgentEvents(on: today)
            print("   agent events after delete: \(after.count) (expect 0)")
        } catch {
            print("   ❌ event flow error: \(error)")
        }
    }

    // MARK: Reminder round-trip

    private static func verifyReminders(adapter: AppleCalendarAdapter, today: Date) async {
        let hash = "verifyrem01"
        let tag = "[agent-reminder:dayflow:\(hash)]"
        let spec = DayflowReminderSpec(
            blockKey: "verify::Read", tagKey: hash, tag: tag,
            title: "Verify Reminder", due: dayString(today),
            notes: "EventKit verify\n\(tag)\n[agent-project:verifyproj]",
            projectID: "verifyproj")

        do {
            print("5) Creating a test reminder via applyReminderChangeset…")
            try await adapter.applyReminderChangeset(
                DayflowReminderChangeset(create: [spec], update: [], delete: [], unchanged: 0))
            let readBack = await adapter.currentAgentReminders()
            print("6) currentAgentReminders read back \(readBack.count): \(readBack.map { $0.title ?? "?" })")
            print("   👉 check Reminders.app — “Verify Reminder” due today")

            print("7) Deleting it…")
            try await adapter.applyReminderChangeset(
                DayflowReminderChangeset(
                    create: [], update: [],
                    delete: [DayflowTagRef(tagKey: hash, tag: tag)], unchanged: 0))
            let after = await adapter.currentAgentReminders()
            print("   agent reminders after delete: \(after.count) (expect 0)")
        } catch {
            print("   ❌ reminder flow error: \(error)")
        }
    }

    // MARK: Helpers

    private static func at(_ hour: Int, _ day: Date) -> Date {
        Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: day) ?? day
    }

    private static func dayString(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}
