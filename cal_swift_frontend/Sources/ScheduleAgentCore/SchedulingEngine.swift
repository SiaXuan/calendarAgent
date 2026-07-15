import Foundation

public struct SchedulingEngine: Sendable {
    private let calendar: Calendar

    public init(calendar: Calendar = Calendar(identifier: .gregorian)) {
        self.calendar = calendar
    }

    public func proposeSchedule(
        tasks: [TaskItem],
        events: [CalendarEvent],
        memories: [MemoryEntry],
        preferences: UserPreferences,
        planningWindow: DateInterval
    ) -> SchedulePlan {
        var busy = events
            .filter { !$0.isMovable || $0.source == .appleCalendar }
            .map { DateInterval(start: $0.start, end: $0.end) }
        var scheduled: [ScheduledBlock] = []
        var unscheduled: [TaskItem] = []
        var questions: [String] = []
        var risks: [ScheduleRisk] = []
        var explanations: [String] = []

        let completeTasks = tasks.filter { task in
            var complete = true
            if task.estimatedMinutes == nil {
                questions.append("How long should I reserve for \(task.title)?")
                complete = false
            }
            if task.deadline == nil {
                questions.append("When is \(task.title) due?")
                complete = false
            }
            if !complete {
                unscheduled.append(task)
            }
            return complete
        }

        let orderedTasks = completeTasks.sorted {
            if $0.priority != $1.priority {
                return $0.priority.rawValue > $1.priority.rawValue
            }
            return ($0.deadline ?? .distantFuture) < ($1.deadline ?? .distantFuture)
        }

        for task in orderedTasks {
            guard let minutes = task.estimatedMinutes, let deadline = task.deadline else {
                continue
            }

            let duration = max(minutes, preferences.minimumBlockMinutes) * 60
            guard let interval = findSlot(
                duration: TimeInterval(duration),
                deadline: deadline,
                energy: task.energy,
                busyIntervals: busy,
                memories: memories,
                preferences: preferences,
                planningWindow: planningWindow
            ) else {
                unscheduled.append(task)
                risks.append(ScheduleRisk(
                    taskTitle: task.title,
                    reason: "Could not fit \(minutes) minutes before its deadline."
                ))
                continue
            }

            let rationale = rationaleFor(task: task, interval: interval, memories: memories)
            let block = ScheduledBlock(
                taskID: task.id,
                taskTitle: task.title,
                start: interval.start,
                end: interval.end,
                rationale: rationale
            )
            scheduled.append(block)
            explanations.append(rationale)
            busy.append(interval)
        }

        return SchedulePlan(
            scheduledBlocks: scheduled.sorted { $0.start < $1.start },
            unscheduledTasks: unscheduled,
            clarifyingQuestions: questions,
            risks: risks,
            explanations: explanations
        )
    }

    private func findSlot(
        duration: TimeInterval,
        deadline: Date,
        energy: EnergyType,
        busyIntervals: [DateInterval],
        memories: [MemoryEntry],
        preferences: UserPreferences,
        planningWindow: DateInterval
    ) -> DateInterval? {
        let latestEnd = min(deadline, planningWindow.end)
        guard planningWindow.start < latestEnd else { return nil }

        var candidates: [DateInterval] = []
        var day = calendar.startOfDay(for: planningWindow.start)
        let finalDay = calendar.startOfDay(for: latestEnd)

        while day <= finalDay {
            guard
                let dayStart = calendar.date(bySettingHour: preferences.workdayStartHour, minute: 0, second: 0, of: day),
                let dayEnd = calendar.date(bySettingHour: preferences.workdayEndHour, minute: 0, second: 0, of: day)
            else {
                day = calendar.date(byAdding: .day, value: 1, to: day) ?? day.addingTimeInterval(24 * 60 * 60)
                continue
            }

            var cursor = max(dayStart, planningWindow.start)
            let endLimit = min(dayEnd, latestEnd)

            while cursor.addingTimeInterval(duration) <= endLimit {
                let interval = DateInterval(start: cursor, duration: duration)
                if !overlapsBusy(interval, busyIntervals: busyIntervals, bufferMinutes: preferences.bufferMinutes) {
                    candidates.append(interval)
                }
                cursor = cursor.addingTimeInterval(15 * 60)
            }

            day = calendar.date(byAdding: .day, value: 1, to: day) ?? day.addingTimeInterval(24 * 60 * 60)
        }

        if energy == .deepWork, prefersMorningDeepWork(memories: memories) {
            let morning = candidates.filter { preferences.deepWorkPreferredHours.contains(calendar.component(.hour, from: $0.start)) }
            return morning.first ?? candidates.first
        }

        return candidates.first
    }

    private func overlapsBusy(_ interval: DateInterval, busyIntervals: [DateInterval], bufferMinutes: Int) -> Bool {
        let buffer = TimeInterval(bufferMinutes * 60)
        return busyIntervals.contains { busy in
            let protected = DateInterval(
                start: busy.start.addingTimeInterval(-buffer),
                end: busy.end.addingTimeInterval(buffer)
            )
            return interval.intersects(protected)
        }
    }

    private func prefersMorningDeepWork(memories: [MemoryEntry]) -> Bool {
        memories.contains { $0.signal == .preferDeepWorkInMorning }
    }

    private func rationaleFor(task: TaskItem, interval: DateInterval, memories: [MemoryEntry]) -> String {
        var reasons = ["Scheduled \(task.title) before its deadline"]
        if task.energy == .deepWork, prefersMorningDeepWork(memories: memories) {
            reasons.append("using your morning deep work preference")
        }
        if let project = task.project {
            reasons.append("for \(project)")
        }
        return reasons.joined(separator: ", ") + "."
    }
}
