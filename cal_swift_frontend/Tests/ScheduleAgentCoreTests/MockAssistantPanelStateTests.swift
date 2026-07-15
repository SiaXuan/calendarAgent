import Testing
import Foundation
@testable import ScheduleAgentCore

@Suite("Mock assistant panel state")
struct MockAssistantPanelStateTests {
    @Test("natural language input creates a parsed task draft")
    func parsesNaturalLanguageInput() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)

        state.parseCommand("Write PRD tomorrow 2h")

        let parsed = try #require(state.parsedTask)
        #expect(parsed.title == "Write PRD")
        #expect(parsed.estimatedMinutes == 120)
        #expect(parsed.priority == .medium)
        #expect(parsed.deadlineLabel == "Tomorrow")
    }

    @Test("adding parsed task moves it into today queue")
    func addParsedTaskToQueue() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        state.parseCommand("Reply to investor 30m")

        state.addParsedTaskToQueue()

        #expect(state.parsedTask == nil)
        #expect(state.todayQueue.map(\.title).contains("Reply to investor"))
    }

    @Test("scheduling one task creates a plan draft")
    func scheduleOneTaskCreatesDraft() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let task = try #require(state.todayQueue.first)

        state.schedule(taskID: task.id)

        let draft = try #require(state.planDraft)
        #expect(draft.blocks.count == 1)
        #expect(draft.blocks.first?.taskID == task.id)
        #expect(draft.risk == nil)
    }

    @Test("confirming a draft moves scheduled tasks from queue to upcoming calendar")
    func confirmMovesQueueTaskToUpcoming() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let task = try #require(state.todayQueue.first)
        state.schedule(taskID: task.id)

        state.confirmDraft()

        #expect(state.planDraft == nil)
        #expect(!state.todayQueue.map(\.id).contains(task.id))
        #expect(state.upcomingEvents.contains { $0.title == task.title && $0.source == .agent })
    }

    @Test("dismissing a draft keeps the queue intact")
    func dismissKeepsQueue() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let originalQueue = state.todayQueue
        let task = try #require(originalQueue.first)
        state.schedule(taskID: task.id)

        state.dismissDraft()

        #expect(state.planDraft == nil)
        #expect(state.todayQueue == originalQueue)
    }

    @Test("syncing the queue moves every task into upcoming calendar")
    func syncAllMovesQueueIntoUpcoming() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let queuedTitles = state.todayQueue.map(\.title)

        state.syncQueueToCalendar(now: fixedNow)

        #expect(state.todayQueue.isEmpty)
        #expect(queuedTitles.allSatisfy { title in
            state.upcomingEvents.contains { $0.title == title && $0.source == .agent }
        })
    }

    @Test("accepting one task moves only that task into upcoming calendar")
    func acceptSingleTaskMovesOnlyOneTask() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let first = try #require(state.todayQueue.first)
        let remaining = state.todayQueue.dropFirst().map(\.id)

        state.acceptSingleTask(taskID: first.id, now: fixedNow)

        #expect(!state.todayQueue.map(\.id).contains(first.id))
        #expect(remaining.allSatisfy { state.todayQueue.map(\.id).contains($0) })
        #expect(state.upcomingEvents.contains { $0.title == first.title && $0.source == .agent })
    }

    @Test("document intake produces routing options")
    func documentIntakeCreatesRoutingOptions() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)

        state.startDocumentIntake(fileName: "ML syllabus.pdf")

        let intake = try #require(state.documentIntake)
        #expect(intake.fileName == "ML syllabus.pdf")
        #expect(intake.steps.allSatisfy { $0.isComplete })
        #expect(intake.routes.map(\.destination) == [.todayQueue, .weeklyPlan, .memory, .clarification])
    }

    @Test("moving a drafted task changes its scheduled time")
    func moveDraftedTaskChangesTime() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let task = try #require(state.todayQueue.first)
        state.schedule(taskID: task.id, now: fixedNow)
        let newStart = fixedNow.addingTimeInterval(3 * 60 * 60)

        state.moveDraftBlock(taskID: task.id, toStart: newStart)

        let block = try #require(state.planDraft?.blocks.first)
        #expect(block.start == newStart)
        #expect(block.end == newStart.addingTimeInterval(TimeInterval((task.estimatedMinutes ?? 30) * 60)))
    }

    @Test("loading calendar events updates current and next calendar status")
    func loadingCalendarEventsUpdatesStatus() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let current = CalendarEvent(
            title: "Live calendar review",
            start: fixedNow.addingTimeInterval(-15 * 60),
            end: fixedNow.addingTimeInterval(30 * 60),
            isMovable: false,
            source: .appleCalendar
        )
        let next = CalendarEvent(
            title: "Advisor sync",
            start: fixedNow.addingTimeInterval(90 * 60),
            end: fixedNow.addingTimeInterval(120 * 60),
            isMovable: false,
            source: .appleCalendar
        )

        state.loadCalendarEvents([next, current], now: fixedNow)

        #expect(state.nowStatus == "In Live calendar review")
        #expect(state.nextEventSummary.contains("Next: Advisor sync"))
        #expect(state.upcomingEvents.map(\.title).prefix(2) == ["Live calendar review", "Advisor sync"])
    }

    @Test("agent plan avoids real calendar busy blocks")
    func agentPlanAvoidsCalendarBusyBlocks() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let busyStart = fixedNow.addingTimeInterval(30 * 60)
        let busyEnd = fixedNow.addingTimeInterval(90 * 60)
        state.loadCalendarEvents([
            CalendarEvent(
                title: "Busy meeting",
                start: busyStart,
                end: busyEnd,
                isMovable: false,
                source: .appleCalendar
            )
        ], now: fixedNow)

        state.planQueueWithAgent(now: fixedNow)

        let draft = try #require(state.planDraft)
        #expect(!draft.blocks.isEmpty)
        #expect(draft.blocks.allSatisfy { block in
            block.end <= busyStart || block.start >= busyEnd
        })
    }

    @Test("dayflow schedule populates energy upcoming and queue metadata")
    func dayflowSchedulePopulatesPanelState() throws {
        var state = MockAssistantPanelState.sample(now: fixedNow)
        let meetingStart = fixedNow.addingTimeInterval(60 * 60)
        let meetingEnd = fixedNow.addingTimeInterval(90 * 60)
        let taskStart = fixedNow.addingTimeInterval(2 * 60 * 60)
        let taskEnd = fixedNow.addingTimeInterval(3 * 60 * 60)
        let schedule = DayflowSchedule(
            date: "2026-06-24",
            energyCurve: [0.2, 0.8, 0.5],
            blocks: [
                DayflowScheduleBlock(
                    start: meetingStart,
                    end: meetingEnd,
                    blockType: .fixed,
                    taskID: nil,
                    title: "Calendar meeting",
                    cognitiveLoad: nil,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 0,
                    breakMinutes: 0,
                    pomodoroCount: 0,
                    deadline: nil
                ),
                DayflowScheduleBlock(
                    start: taskStart,
                    end: taskEnd,
                    blockType: .scheduled,
                    taskID: "task-123",
                    title: "Write assignment",
                    cognitiveLoad: .deep,
                    notes: "Use high-energy block",
                    phaseLabel: "Draft",
                    focusMinutes: 50,
                    breakMinutes: 10,
                    pomodoroCount: 1,
                    deadline: taskEnd.addingTimeInterval(24 * 60 * 60)
                )
            ],
            unscheduled: [],
            healthSummary: "Energy peaks late morning."
        )

        state.applyDayflowSchedule(schedule, now: fixedNow)

        let queued = try #require(state.todayQueue.first)
        #expect(state.energyCurve == [0.2, 0.8, 0.5])
        #expect(state.healthSignal.summary == "Energy peaks late morning.")
        #expect(state.upcomingEvents.map(\.title) == ["Calendar meeting"])
        #expect(queued.title == "Write assignment")
        #expect(queued.energy == .deepWork)
        #expect(state.backendStart(for: queued.id) == taskStart)
        #expect(state.backendScheduleMetadata(for: queued.id)?.end == taskEnd)
        #expect(state.backendScheduleMetadata(for: queued.id)?.focusMinutes == 50)
        #expect(state.backendScheduleMetadata(for: queued.id)?.breakMinutes == 10)
        #expect(state.backendBlockKey(for: queued.id) == "task-123::Write assignment")
    }

    @Test("dayflow schedule keeps backend task kind for task badges")
    func dayflowScheduleKeepsBackendTaskKindForBadges() throws {
        var state = MockAssistantPanelState.loading()
        let taskStart = fixedNow.addingTimeInterval(2 * 60 * 60)
        let taskEnd = fixedNow.addingTimeInterval(150 * 60)
        let schedule = DayflowSchedule(
            date: "2026-06-24",
            energyCurve: [0.5],
            blocks: [
                DayflowScheduleBlock(
                    start: taskStart,
                    end: taskEnd,
                    blockType: .scheduled,
                    taskID: "task-analytical",
                    title: "Study transformer internals",
                    cognitiveLoad: .deep,
                    taskKind: "analytical",
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 25,
                    breakMinutes: 5,
                    pomodoroCount: 1,
                    deadline: taskEnd
                )
            ],
            unscheduled: [],
            healthSummary: "ok"
        )

        state.applyDayflowSchedule(schedule, now: fixedNow)

        let task = try #require(state.todayQueue.first)
        #expect(state.backendScheduleMetadata(for: task.id)?.backendBadgeLabel == "ANALYTICAL")
    }

    @Test("backend loading and error states do not keep sample data")
    func backendStatesDoNotKeepSampleData() throws {
        var state = MockAssistantPanelState.loading()

        #expect(state.todayQueue.isEmpty)
        #expect(state.upcomingEvents.isEmpty)
        #expect(state.nowStatus == "Connecting Dayflow")

        state = MockAssistantPanelState.sample(now: fixedNow)
        state.clearForBackendError("Dayflow backend unavailable")

        #expect(state.todayQueue.isEmpty)
        #expect(state.upcomingEvents.isEmpty)
        #expect(state.planDraft == nil)
        #expect(state.parsedTask == nil)
        #expect(state.nowStatus == "Backend unavailable")
        #expect(state.statusMessage == "Dayflow backend unavailable")
    }

    @Test("stream events progressively update health fixed blocks and schedule")
    func streamEventsProgressivelyUpdatePanelState() throws {
        var state = MockAssistantPanelState.loading()
        let meetingStart = fixedNow.addingTimeInterval(30 * 60)
        let meetingEnd = fixedNow.addingTimeInterval(60 * 60)
        let taskStart = fixedNow.addingTimeInterval(90 * 60)
        let taskEnd = fixedNow.addingTimeInterval(150 * 60)

        state.beginDayflowStream()
        state.applyDayflowHealth(energyCurve: [0.1, 0.8, 0.4], healthSummary: "Health arrived.")

        #expect(state.energyCurve == [0.1, 0.8, 0.4])
        #expect(state.healthSignal.hasExternalData == false)
        #expect(state.healthSignal.sleepWindow == "No data")
        #expect(state.todayQueue.isEmpty)
        #expect(state.statusMessage == "Loaded energy curve from Dayflow.")

        state.applyDayflowFixedBlocks([
            DayflowScheduleBlock(
                start: meetingStart,
                end: meetingEnd,
                blockType: .fixed,
                taskID: nil,
                title: "Advisor meeting",
                cognitiveLoad: nil,
                notes: nil,
                phaseLabel: nil,
                focusMinutes: 0,
                breakMinutes: 0,
                pomodoroCount: 0,
                deadline: nil
            )
        ], now: fixedNow)

        #expect(state.upcomingEvents.map(\.title) == ["Advisor meeting"])
        #expect(state.todayQueue.isEmpty)

        state.applyDayflowScheduleBlocks(
            date: "2026-06-24",
            blocks: [
                DayflowScheduleBlock(
                    start: meetingStart,
                    end: meetingEnd,
                    blockType: .fixed,
                    taskID: nil,
                    title: "Advisor meeting",
                    cognitiveLoad: nil,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 0,
                    breakMinutes: 0,
                    pomodoroCount: 0,
                    deadline: nil
                ),
                DayflowScheduleBlock(
                    start: taskStart,
                    end: taskEnd,
                    blockType: .scheduled,
                    taskID: "task-456",
                    title: "Draft research note",
                    cognitiveLoad: .deep,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 50,
                    breakMinutes: 10,
                    pomodoroCount: 1,
                    deadline: taskEnd
                )
            ],
            unscheduled: [],
            now: fixedNow
        )

        #expect(state.healthSignal.summary == "Health arrived.")
        #expect(state.upcomingEvents.map(\.title) == ["Advisor meeting"])
        #expect(state.todayQueue.map(\.title) == ["Draft research note"])
    }

    @Test("energy source none keeps empty curve and asks for manual health input")
    func energySourceNoneKeepsEmptyCurveAndCTA() throws {
        var state = MockAssistantPanelState.loading()

        state.applyDayflowHealth(
            energyCurve: [],
            healthSummary: "No health signal.",
            energySource: .none
        )

        #expect(state.energyCurve.isEmpty)
        #expect(state.healthSignal.energySource == .none)
        #expect(state.shouldPromptForHealthInput)
        #expect(state.statusMessage == "No energy data yet. Add sleep input to unlock the curve.")
    }

    @Test("baseline energy source renders as available energy data")
    func baselineEnergySourceIsAvailable() throws {
        var state = MockAssistantPanelState.loading()

        state.applyDayflowHealth(
            energyCurve: [0.2, 0.5, 0.8],
            healthSummary: "Using baseline.",
            energySource: .baseline
        )

        #expect(state.energyCurve == [0.2, 0.5, 0.8])
        #expect(state.healthSignal.energySource == .baseline)
        #expect(!state.shouldPromptForHealthInput)
    }

    @Test("manual sleep input normalizes same-day post-midnight window")
    func manualSleepInputNormalizesSameDayPostMidnightWindow() throws {
        let calendar = Calendar(identifier: .gregorian)
        let pickedStart = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 0, minute: 0))!
        let pickedEnd = calendar.date(from: DateComponents(year: 2026, month: 7, day: 1, hour: 9, minute: 30))!

        let normalized = try #require(ManualSleepWindowNormalizer.normalized(
            sleepStart: pickedStart,
            sleepEnd: pickedEnd,
            targetDate: "2026-06-30",
            calendar: calendar
        ))

        #expect(normalized.start == calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 0, minute: 0)))
        #expect(normalized.end == calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 9, minute: 30)))
    }

    @Test("manual sleep input normalizes overnight window")
    func manualSleepInputNormalizesOvernightWindow() throws {
        let calendar = Calendar(identifier: .gregorian)
        let pickedStart = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 23, minute: 40))!
        let pickedEnd = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 7, minute: 15))!

        let normalized = try #require(ManualSleepWindowNormalizer.normalized(
            sleepStart: pickedStart,
            sleepEnd: pickedEnd,
            targetDate: "2026-06-30",
            calendar: calendar
        ))

        #expect(normalized.start == calendar.date(from: DateComponents(year: 2026, month: 6, day: 29, hour: 23, minute: 40)))
        #expect(normalized.end == calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 7, minute: 15)))
    }

    @Test("health snapshot updates sleep window without locking manual input")
    func healthSnapshotUpdatesSleepWindowWithoutLockingManualInput() throws {
        var state = MockAssistantPanelState.loading()
        let calendar = Calendar(identifier: .gregorian)
        let sleepStart = calendar.date(from: DateComponents(year: 2026, month: 6, day: 29, hour: 23, minute: 40))!
        let sleepEnd = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 7, minute: 15))!

        state.applyHealthSnapshot(
            sleepStart: sleepStart,
            sleepEnd: sleepEnd,
            restingHeartRate: 58,
            hrv: 42,
            steps: 8_500
        )

        #expect(state.healthSignal.sleepWindow == "23:40-07:15")
        #expect(state.healthSignal.restingHeartRate == 58)
        #expect(state.healthSignal.hrv == 42)
        #expect(state.healthSignal.steps == 8_500)
        #expect(state.healthSignal.hasExternalData == false)
    }

    @Test("manual sleep update stays available and preserves health metrics")
    func manualSleepUpdatePreservesMetrics() throws {
        var state = MockAssistantPanelState.loading()
        let calendar = Calendar(identifier: .gregorian)
        let snapshotStart = calendar.date(from: DateComponents(year: 2026, month: 6, day: 29, hour: 23, minute: 40))!
        let snapshotEnd = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 7, minute: 15))!
        let manualStart = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 0, minute: 10))!
        let manualEnd = calendar.date(from: DateComponents(year: 2026, month: 6, day: 30, hour: 7, minute: 40))!

        state.applyHealthSnapshot(
            sleepStart: snapshotStart,
            sleepEnd: snapshotEnd,
            restingHeartRate: 58,
            hrv: 42,
            steps: 8_500
        )
        state.applyManualSleepWindow(sleepStart: manualStart, sleepEnd: manualEnd)

        #expect(state.healthSignal.sleepWindow == "00:10-07:40")
        #expect(state.healthSignal.restingHeartRate == 58)
        #expect(state.healthSignal.hrv == 42)
        #expect(state.healthSignal.steps == 8_500)
        #expect(state.healthSignal.hasExternalData == false)
    }

    @Test("schedule metadata adjusts duration by full pomodoro chunks")
    func scheduleMetadataAdjustsDurationByPomodoroChunks() {
        let metadata = DayflowTaskScheduleMetadata(
            start: fixedNow,
            end: fixedNow.addingTimeInterval(30 * 60),
            focusMinutes: 25,
            breakMinutes: 5,
            pomodoroCount: 1
        )

        #expect(metadata.durationMinutes(adjustingPomodoroCountBy: 1) == 55)
        #expect(metadata.durationMinutes(adjustingPomodoroCountBy: -1) == 25)
    }

    @Test("pomodoro label shows session count and total duration")
    func pomodoroLabelShowsSessionCountAndTotalDuration() {
        let one = DayflowTaskScheduleMetadata(
            start: fixedNow,
            end: fixedNow.addingTimeInterval(30 * 60),
            focusMinutes: 25,
            breakMinutes: 5,
            pomodoroCount: 1
        )
        let two = DayflowTaskScheduleMetadata(
            start: fixedNow,
            end: fixedNow.addingTimeInterval(55 * 60),
            focusMinutes: 25,
            breakMinutes: 5,
            pomodoroCount: 2
        )

        #expect(one.pomodoroSessionLabel == "1 x 25m · 25+5m")
        #expect(two.pomodoroSessionLabel == "2 x 25m · 55m")
    }

    @Test("synced block keys mark and survive schedule reload")
    func syncedBlockKeysMarkAndSurviveScheduleReload() {
        var state = MockAssistantPanelState.loading()
        let key = "task-1::Write note"

        state.markBackendBlockSynced(key)
        state.applyDayflowHealth(energyCurve: [0.5], healthSummary: "ok", energySource: .today)

        #expect(state.isBackendBlockSynced(key))
    }

    @Test("timeline drop maps y to snapped workday time")
    func timelineDropMapsYToSnappedWorkdayTime() throws {
        let calendar = Calendar(identifier: .gregorian)
        let mapper = ScheduleTimelineDropMapper(
            targetDate: "2026-06-24",
            workStartHour: 8,
            workEndHour: 22,
            snapMinutes: 15,
            calendar: calendar
        )

        let top = try #require(mapper.date(forY: 0, inHeight: 560))
        let middle = try #require(mapper.date(forY: 250, inHeight: 560))
        let bottom = try #require(mapper.date(forY: 560, inHeight: 560))

        #expect(top == calendar.date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 8, minute: 0)))
        #expect(middle == calendar.date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 14, minute: 15)))
        #expect(bottom == calendar.date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 22, minute: 0)))
    }

    @Test("drag target resolver chooses card under pointer and ignores dragged card")
    func dragTargetResolverChoosesCardUnderPointer() throws {
        let sourceID = UUID()
        let targetID = UUID()
        let otherID = UUID()
        let frames: [UUID: CGRect] = [
            sourceID: CGRect(x: 0, y: 0, width: 300, height: 44),
            targetID: CGRect(x: 0, y: 52, width: 300, height: 44),
            otherID: CGRect(x: 0, y: 104, width: 300, height: 44)
        ]

        #expect(ScheduleDragTargetResolver.targetTaskID(forY: 60, draggedTaskID: sourceID, frames: frames) == targetID)
        #expect(ScheduleDragTargetResolver.targetTaskID(forY: 20, draggedTaskID: sourceID, frames: frames) == nil)
        #expect(ScheduleDragTargetResolver.targetTaskID(forY: 112, draggedTaskID: sourceID, frames: frames) == otherID)
    }

    @Test("upcoming lane combines scheduled tasks and calendar events with in-progress marker")
    func upcomingLaneCombinesTasksAndCalendarEvents() throws {
        var state = MockAssistantPanelState.loading()
        let currentStart = fixedNow.addingTimeInterval(-10 * 60)
        let currentEnd = fixedNow.addingTimeInterval(20 * 60)
        let taskStart = fixedNow.addingTimeInterval(40 * 60)
        let taskEnd = fixedNow.addingTimeInterval(65 * 60)
        let schedule = DayflowSchedule(
            date: "2026-06-24",
            energyCurve: [0.5],
            blocks: [
                DayflowScheduleBlock(
                    start: currentStart,
                    end: currentEnd,
                    blockType: .fixed,
                    taskID: nil,
                    title: "Current meeting",
                    cognitiveLoad: nil,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 0,
                    breakMinutes: 0,
                    pomodoroCount: 0,
                    deadline: nil
                ),
                DayflowScheduleBlock(
                    start: taskStart,
                    end: taskEnd,
                    blockType: .scheduled,
                    taskID: "task-789",
                    title: "Write Swift frontend",
                    cognitiveLoad: .deep,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 25,
                    breakMinutes: 5,
                    pomodoroCount: 1,
                    deadline: taskEnd
                )
            ],
            unscheduled: [],
            healthSummary: "ok"
        )

        state.applyDayflowSchedule(schedule, now: fixedNow)

        let entries = state.upcomingLaneEntries(now: fixedNow)
        let scheduledEntries = entries.filter { $0.kind != .dropSlot }

        #expect(scheduledEntries.map(\.title) == ["Current meeting", "Write Swift frontend"])
        #expect(scheduledEntries.first?.kind == .calendar)
        #expect(scheduledEntries.first?.isInProgress == true)
        #expect(scheduledEntries.last?.kind == .agentTask)
        #expect(scheduledEntries.last?.isInProgress == false)
    }

    @Test("upcoming lane offers evening drop slots after last scheduled block")
    func upcomingLaneOffersEveningDropSlots() throws {
        var state = MockAssistantPanelState.loading()
        let dinnerStart = Calendar(identifier: .gregorian).date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 18))!
        let dinnerEnd = Calendar(identifier: .gregorian).date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 18, minute: 50))!
        let schedule = DayflowSchedule(
            date: "2026-06-24",
            energyCurve: [0.5],
            blocks: [
                DayflowScheduleBlock(
                    start: dinnerStart,
                    end: dinnerEnd,
                    blockType: .meal,
                    taskID: nil,
                    title: "晚餐休息",
                    cognitiveLoad: nil,
                    notes: nil,
                    phaseLabel: nil,
                    focusMinutes: 0,
                    breakMinutes: 0,
                    pomodoroCount: 0,
                    deadline: nil
                )
            ],
            unscheduled: [],
            healthSummary: "ok"
        )

        state.applyDayflowSchedule(schedule, now: fixedNow)

        let entries = state.upcomingLaneEntries(now: fixedNow)

        #expect(entries.contains(where: { $0.kind == .dropSlot && $0.start == dinnerStart.addingTimeInterval(60 * 60) }))
        #expect(entries.contains(where: { $0.kind == .dropSlot && $0.title == "Drop at 19:00" }))
    }

    private var fixedNow: Date {
        Calendar(identifier: .gregorian).date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 9, minute: 30))!
    }
}
