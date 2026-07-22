import Foundation

/// Manual on-device check for the Phase 4 EventKit executor. Because macOS TCC
/// only prompts when the app is launched as a bundle by LaunchServices, run it
/// via `open` (see make_app.sh output) — which means stdout doesn't reach the
/// terminal, so every line is ALSO written to ~/eventkit-verify.log.
///
///     ./make_app.sh
///     open -W ScheduleAgent.app --args --verify-eventkit
///     cat ~/eventkit-verify.log
///
/// It talks ONLY to the local Calendar/Reminders via AppleCalendarAdapter — no
/// backend — building change-sets by hand and applying them, so a clean run
/// proves the executor create/read/delete round-trip works.
enum EventKitVerification {

    private static let logURL = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("eventkit-verify.log")

    static func run() {
        try? "".write(to: logURL, atomically: true, encoding: .utf8)   // reset log
        let semaphore = DispatchSemaphore(value: 0)
        Task.detached {
            await performVerification()
            semaphore.signal()
        }
        semaphore.wait()
    }

    /// Print to stdout AND append to the log file (so `open`-launched runs, whose
    /// stdout is detached, are still inspectable).
    private static func emit(_ line: String) {
        print(line)
        guard let data = (line + "\n").data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: logURL)
        }
    }

    private static func performVerification() async {
        let adapter = AppleCalendarAdapter()

        emit("1) Requesting Calendar + Reminders access…")
        let (granted, error) = await adapter.requestFullAccess()
        guard granted else {
            emit("   ❌ access NOT granted: \(error?.localizedDescription ?? "denied").")
            emit("      Launch via `open ScheduleAgent.app --args --verify-eventkit` so macOS")
            emit("      prompts for the app itself, then allow Calendar AND Reminders.")
            return
        }
        emit("   ✅ access granted")

        let today = Date()
        await verifyEvents(adapter: adapter, today: today)
        await verifyReminders(adapter: adapter, today: today)

        emit("")
        emit("Done. If steps 2 & 5 showed the item appear in Calendar/Reminders and")
        emit("steps 4 & 7 reported 0 afterwards, the EventKit executor works.")
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
            emit("2) Creating a test event via applyEventChangeset…")
            try adapter.applyEventChangeset(
                DayflowEventChangeset(create: [spec], update: [], delete: [], unchanged: 0),
                on: today)
            let readBack = adapter.currentAgentEvents(on: today)
            emit("3) currentAgentEvents read back \(readBack.count): \(readBack.map { $0.title ?? "?" })")
            emit("   👉 check Calendar.app — “Verify Focus Block” at 09:00 today, in a “Schedule Agent” calendar")

            emit("4) Deleting it via a delete change-set…")
            try adapter.applyEventChangeset(
                DayflowEventChangeset(
                    create: [], update: [],
                    delete: [DayflowTagRef(tagKey: hash, tag: tag)], unchanged: 0),
                on: today)
            let after = adapter.currentAgentEvents(on: today)
            emit("   agent events after delete: \(after.count) (expect 0)")
        } catch {
            emit("   ❌ event flow error: \(error)")
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
            emit("5) Creating a test reminder via applyReminderChangeset…")
            try await adapter.applyReminderChangeset(
                DayflowReminderChangeset(create: [spec], update: [], delete: [], unchanged: 0))
            let readBack = await adapter.currentAgentReminders()
            emit("6) currentAgentReminders read back \(readBack.count): \(readBack.map { $0.title ?? "?" })")
            emit("   👉 check Reminders.app — “Verify Reminder” due today")

            emit("7) Deleting it…")
            try await adapter.applyReminderChangeset(
                DayflowReminderChangeset(
                    create: [], update: [],
                    delete: [DayflowTagRef(tagKey: hash, tag: tag)], unchanged: 0))
            let after = await adapter.currentAgentReminders()
            emit("   agent reminders after delete: \(after.count) (expect 0)")
        } catch {
            emit("   ❌ reminder flow error: \(error)")
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
