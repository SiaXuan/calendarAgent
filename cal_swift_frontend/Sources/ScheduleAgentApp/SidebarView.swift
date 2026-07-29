import SwiftUI
import UniformTypeIdentifiers
import ScheduleAgentCore

private struct QueueRowFramePreferenceKey: PreferenceKey {
    static let defaultValue: [UUID: CGRect] = [:]

    static func reduce(value: inout [UUID: CGRect], nextValue: () -> [UUID: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, next in next })
    }
}

private struct TimelineHeightPreferenceKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct CommandTextField: NSViewRepresentable {
    @Binding var text: String
    @Binding var isFocused: Bool

    let placeholder: String
    let onFocusRequest: () -> Void
    let onSubmit: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, isFocused: $isFocused, onFocusRequest: onFocusRequest, onSubmit: onSubmit)
    }

    func makeNSView(context: Context) -> FocusableCommandNSTextField {
        let field = FocusableCommandNSTextField()
        field.delegate = context.coordinator
        field.onFocusRequest = context.coordinator.focusRequested
        field.onSubmit = context.coordinator.submit
        field.stringValue = text
        field.placeholderString = placeholder
        field.isBezeled = false
        field.isBordered = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.font = .systemFont(ofSize: NSFont.systemFontSize)
        field.textColor = .labelColor
        field.placeholderAttributedString = NSAttributedString(
            string: placeholder,
            attributes: [
                .foregroundColor: NSColor.placeholderTextColor,
                .font: NSFont.systemFont(ofSize: NSFont.systemFontSize)
            ]
        )
        return field
    }

    func updateNSView(_ nsView: FocusableCommandNSTextField, context: Context) {
        context.coordinator.text = $text
        context.coordinator.isFocused = $isFocused
        nsView.onFocusRequest = context.coordinator.focusRequested
        nsView.onSubmit = context.coordinator.submit
        if nsView.stringValue != text {
            nsView.stringValue = text
        }
        if isFocused, nsView.window?.firstResponder !== nsView.currentEditor() {
            DispatchQueue.main.async {
                nsView.window?.makeFirstResponder(nsView)
            }
        }
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var text: Binding<String>
        var isFocused: Binding<Bool>
        let onFocusRequest: () -> Void
        let onSubmit: () -> Void

        init(text: Binding<String>, isFocused: Binding<Bool>, onFocusRequest: @escaping () -> Void, onSubmit: @escaping () -> Void) {
            self.text = text
            self.isFocused = isFocused
            self.onFocusRequest = onFocusRequest
            self.onSubmit = onSubmit
        }

        func focusRequested(_ field: NSTextField) {
            onFocusRequest()
            isFocused.wrappedValue = true
            DispatchQueue.main.async {
                field.window?.makeFirstResponder(field)
            }
        }

        func submit() {
            onSubmit()
        }

        func controlTextDidBeginEditing(_ notification: Notification) {
            isFocused.wrappedValue = true
        }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            text.wrappedValue = field.stringValue
        }

        func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            if commandSelector == #selector(NSResponder.insertNewline(_:)) {
                guard text.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false else {
                    return true
                }
                onSubmit()
                return true
            }
            return false
        }

        func controlTextDidEndEditing(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            text.wrappedValue = field.stringValue
        }
    }
}

private final class FocusableCommandNSTextField: NSTextField {
    var onFocusRequest: ((NSTextField) -> Void)?
    var onSubmit: (() -> Void)?

    override var acceptsFirstResponder: Bool { true }
    override var needsPanelToBecomeKey: Bool { true }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func mouseDown(with event: NSEvent) {
        onFocusRequest?(self)
        super.mouseDown(with: event)
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 || event.keyCode == 76 {
            onSubmit?()
        } else {
            super.keyDown(with: event)
        }
    }
}

struct SidebarView: View {
    let isPinned: Bool
    let onTogglePin: () -> Void

    @Environment(\.colorScheme) private var colorScheme

    @State private var state = MockAssistantPanelState.loading()
    @State private var commandText = ""
    @State private var selectedEventID: UUID?
    @State private var isShowingHealthDetails = false
    @State private var isShowingDocumentImporter = false
    @State private var pendingAgentProposal: DayflowAgentProposal?
    @State private var pendingAgentProposalMessage: String?
    @State private var scheduleDate = Self.todayString()
    @State private var isLoadingBackend = false
    @State private var streamTask: Task<Void, Never>?
    @State private var resizeDebounceTasks: [UUID: Task<Void, Never>] = [:]
    @State private var queueRowFrames: [UUID: CGRect] = [:]
    @State private var draggingTaskID: UUID?
    @State private var dragTargetTaskID: UUID?
    @State private var dragOffset: CGSize = .zero
    @State private var upcomingTimelineHeight: CGFloat = 0
    @State private var dragDropTime: Date?
    @State private var dragDropY: CGFloat?
    @FocusState private var commandFocused: Bool

    private let calendarAdapter = AppleCalendarAdapter()
    private let dayflowClient = DayflowAPIClient()
    private let agentChatRefreshPolicy = AgentChatRefreshPolicy()
    private var subtleControlFill: Color {
        Color(nsColor: .controlBackgroundColor).opacity(0.72)
    }
    private var commandColor: Color { Color(nsColor: .systemBlue) }
    private var taskColor: Color { Color(nsColor: .systemBlue) }
    private var calendarColor: Color { Color(nsColor: .systemRed) }
    private var activeColor: Color { Color(nsColor: .systemGreen) }
    private var syncedColor: Color { Color(nsColor: .systemBlue) }
    private var quietColor: Color { Color(nsColor: .secondaryLabelColor) }
    /// Row-control glyph tint. These controls live in the Upcoming lane, whose
    /// card fill is INVERTED vs the system appearance (a dark card in light
    /// mode, light card in dark mode). So the glyph must track the lane's own
    /// text color — same as the (clearly legible) task titles — not the raw
    /// colorScheme, or it lands dark-on-dark.
    private var controlGlyphColor: Color { upcomingPrimaryColor }
    private var energyCaption: String {
        switch state.healthSignal.energySource {
        case .none:
            "Add sleep input"
        case .baseline:
            state.healthSignal.sleepWindow == "No data" ? "Baseline available" : "Baseline \(state.healthSignal.sleepWindow)"
        case .today:
            state.healthSignal.sleepWindow == "No data" ? "Health data loaded" : "Sleep \(state.healthSignal.sleepWindow)"
        }
    }
    private let cardCornerRadius: CGFloat = 18
    private let cardStrokeOpacity = 0.34
    private let cardStrokeWidth = 0.9
    private let cardShadowOpacity = 0.16
    private let cardShadowRadius: CGFloat = 4.5
    private let cardShadowYOffset: CGFloat = 2.2

    var body: some View {
        VStack(spacing: 0) {
            ScrollView(showsIndicators: false) {
                VStack(spacing: 16) {
                    commandModule
                    quickControls
                    energyCurveModule
                    documentIntakeModule
                    parsedTaskModule
                    agentProposalModule
                    planDraftModule
                    upcomingModule
                }
                .padding(12)
            }
            statusBar
        }
        .tint(Color.primary)
        .background(Color.clear)
        .sheet(isPresented: $isShowingHealthDetails) {
            HealthDetailView(signal: state.healthSignal, curve: state.energyCurve) { sleepStart, sleepEnd in
                submitManualSleepWindow(sleepStart: sleepStart, sleepEnd: sleepEnd)
            }
        }
        .fileImporter(isPresented: $isShowingDocumentImporter, allowedContentTypes: [.item]) { result in
            if case let .success(url) = result {
                state.startDocumentIntake(fileName: url.lastPathComponent)
            }
        }
        .onAppear {
            // Don't prompt for calendar access on launch — the reminders
            // full-access flow (import / replan) grants it, and generation reads
            // the local calendar only when it's already authorized. Prompting
            // here re-fires every launch under ad-hoc signing (TCC resets).
            loadTodaySchedulePreferringCache()
        }
        .onDisappear {
            streamTask?.cancel()
            streamTask = nil   // else the guard in startDayflowStream blocks a restart
        }
    }

    private var headerModule: some View {
        controlModule(accent: activeColor) {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    Circle()
                        .fill(activeColor.opacity(0.09))
                    Image(systemName: "sparkles")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(activeColor)
                }
                .frame(width: 38, height: 38)

                VStack(alignment: .leading, spacing: 4) {
                    Text(state.nowStatus)
                        .font(.system(size: 17, weight: .semibold))
                        .lineLimit(2)
                    Text(state.nextEventSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer()

                iconButton(systemName: isPinned ? "pin.fill" : "pin", help: isPinned ? "Unpin sidebar" : "Pin sidebar") {
                    onTogglePin()
                }
            }
        }
    }

    private var commandModule: some View {
        controlModule(accent: commandColor) {
            HStack(spacing: 10) {
                Image(systemName: "text.cursor")
                    .foregroundStyle(commandColor)
                    .frame(width: 18)
                CommandTextField(
                    text: $commandText,
                    isFocused: Binding(
                        get: { commandFocused },
                        set: { commandFocused = $0 }
                    ),
                    placeholder: "Ask the schedule agent...",
                    onFocusRequest: prepareCommandInput,
                    onSubmit: parseCommand
                )
                .frame(maxWidth: .infinity, minHeight: 22, maxHeight: 22, alignment: .leading)
                .layoutPriority(1)
                Button {
                    prepareCommandInput()
                    parseCommand()
                } label: {
                    Image(systemName: "arrow.turn.down.left")
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 26, height: 24)
                }
                .buttonStyle(.borderless)
                .disabled(commandText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .help("Ask agent")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background {
                RoundedRectangle(cornerRadius: 11)
                    .fill(Color(nsColor: .textBackgroundColor).opacity(commandFocused ? 0.42 : 0.28))
            }
            .overlay {
                RoundedRectangle(cornerRadius: 11)
                    .strokeBorder(commandFocused ? commandColor.opacity(0.24) : Color.primary.opacity(0.07), lineWidth: 0.75)
            }
            .contentShape(Rectangle())
            .onTapGesture {
                prepareCommandInput()
            }
        }
    }

    private var quickControls: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
            controlTile(title: "Projects", subtitle: "Import & plan", systemName: "folder", color: taskColor) {
                ProjectsWindowController.shared.show()
            }
            controlTile(title: "Calendar", subtitle: "Open app", systemName: "calendar", color: calendarColor) {
                calendarAdapter.openInCalendar(near: Date())
            }
        }
    }

    /// Force a fresh generation of today's schedule (reads the current local
    /// calendar + health), streaming the result so the energy curve refreshes.
    private func regenerateToday() {
        guard !isLoadingBackend else { return }
        streamTask?.cancel()
        streamTask = nil   // clear the guard in startDayflowStream
        startDayflowStream()
    }

    private var energyCurveModule: some View {
        Button {
            isShowingHealthDetails = true
        } label: {
            controlModule(accent: activeColor) {
                VStack(alignment: .leading, spacing: 7) {
                    HStack {
                        moduleHeader("Energy", systemName: "waveform.path.ecg", caption: energyCaption, color: activeColor)
                        Spacer()
                        Image(systemName: "info.circle")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.secondary)
                    }
                    if state.shouldPromptForHealthInput {
                        energyInputCTA(compact: true)
                    } else {
                        EnergyCurveView(values: state.energyCurve, accent: activeColor)
                            .frame(height: 50)
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func energyInputCTA(compact: Bool) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "bed.double")
                .font(.system(size: compact ? 13 : 16, weight: .semibold))
                .foregroundStyle(activeColor)
                .frame(width: compact ? 18 : 22)
            VStack(alignment: .leading, spacing: 2) {
                Text("Add sleep input")
                    .font(.system(size: compact ? 12 : 14, weight: .semibold))
                    .foregroundStyle(.primary)
                Text("Energy curve is blank until sleep data arrives.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(compact ? 1 : 2)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, compact ? 9 : 10)
        .padding(.vertical, compact ? 8 : 10)
        .background(rowFill)
        .clipShape(RoundedRectangle(cornerRadius: 11))
    }

    @ViewBuilder
    private var documentIntakeModule: some View {
        if let intake = state.documentIntake {
            controlModule(accent: taskColor) {
                VStack(alignment: .leading, spacing: 10) {
                    moduleHeader("Document Intake", systemName: "doc.text.magnifyingglass", caption: intake.fileName, color: taskColor)
                    HStack(spacing: 6) {
                        ForEach(intake.steps) { step in
                            Label(step.title, systemImage: step.isComplete ? "checkmark.circle.fill" : "circle")
                                .font(.caption2)
                                .foregroundStyle(step.isComplete ? activeColor : .secondary)
                                .labelStyle(.titleAndIcon)
                        }
                    }
                    VStack(spacing: 7) {
                        ForEach(intake.routes) { route in
                            HStack(spacing: 8) {
                                Image(systemName: routeIcon(route.destination))
                                    .foregroundStyle(routeColor(route.destination))
                                    .frame(width: 18)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(route.title)
                                        .font(.system(size: 13, weight: .semibold))
                                        .lineLimit(1)
                                    Text(route.rationale)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                                Spacer()
                            }
                            .padding(8)
                            .background(rowFill)
                            .clipShape(RoundedRectangle(cornerRadius: 11))
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var parsedTaskModule: some View {
        if let parsed = state.parsedTask {
            controlModule(accent: taskColor) {
                VStack(alignment: .leading, spacing: 10) {
                    moduleHeader("Parsed Task", systemName: "doc.text.magnifyingglass", caption: "Review before adding", color: taskColor)
                    Text(parsed.title)
                        .font(.system(size: 15, weight: .semibold))
                        .lineLimit(2)
                    HStack(spacing: 6) {
                        capsuleLabel("\(parsed.estimatedMinutes)m", color: taskColor)
                        capsuleLabel(parsed.deadlineLabel, color: calendarColor)
                        capsuleLabel(priorityLabel(parsed.priority), color: priorityColor(parsed.priority))
                    }
                    HStack {
                        Button("Add to Queue") {
                            state.addParsedTaskToQueue()
                            commandText = ""
                        }
                        .buttonStyle(.borderedProminent)
                        Button("Edit") {
                            commandText = parsed.originalInput
                            commandFocused = true
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
        }
    }

    private var todayQueueModule: some View {
        controlModule {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    moduleHeader("Today Queue", systemName: "tray.full", caption: "\(state.todayQueue.count) waiting")
                    Spacer()
                    Button {
                        syncQueueToCalendar()
                    } label: {
                        Label(isLoadingBackend ? "Syncing" : "Sync All", systemImage: "calendar.badge.plus")
                    }
                    .buttonStyle(.bordered)
                    .disabled(state.todayQueue.isEmpty || isLoadingBackend)
                }

                if state.todayQueue.isEmpty {
                    emptyText("No tasks waiting. Add one with the command field.")
                } else {
                    VStack(spacing: 8) {
                        ForEach(state.todayQueue) { task in
                            queueTaskRow(task)
                        }
                    }
                    .coordinateSpace(name: "todayQueue")
                    .onPreferenceChange(QueueRowFramePreferenceKey.self) { frames in
                        queueRowFrames = frames
                    }
                }
            }
        }
    }

    private func queueTaskRow(_ task: TaskItem) -> some View {
        let isDragging = draggingTaskID == task.id
        let metadata = state.backendScheduleMetadata(for: task.id)
        let isCurrent = metadata.map { $0.start <= Date() && Date() < $0.end } ?? false
        let blockKey = state.backendBlockKey(for: task.id)
        let isSynced = blockKey.map { state.isBackendBlockSynced($0) } ?? false
        let isDone = metadata?.isDone ?? false
        let foreground = isCurrent ? activeColor : upcomingPrimaryColor
        let secondary = upcomingSecondaryColor
        let backendBadgeLabel = metadata?.backendBadgeLabel

        return HStack(spacing: 10) {
            if let metadata {
                timelineStamp(start: metadata.start, end: metadata.end, isCurrent: isCurrent)
            } else {
                Text(taskDurationLabel(task))
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(secondary)
                    .frame(width: 56, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(task.title)
                    .font(.system(size: isCurrent ? 14 : 13, weight: isCurrent ? .semibold : .medium))
                    .foregroundStyle(isDone ? secondary : foreground)
                    .strikethrough(isDone, color: secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    Text(taskTimingLabel(task))
                        .font(.caption)
                        .foregroundStyle(isCurrent ? activeColor : secondary)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)  // keep the timing legible; the badge yields, not this
                        .layoutPriority(1)
                    if let backendBadgeLabel {
                        backendTaskBadge(backendBadgeLabel, cognitiveLoad: metadata?.cognitiveLoad)
                    } else {
                        priorityBadge(task.priority, prominent: false)
                    }
                }
            }
            Spacer()
            if metadata != nil && !isSynced {
                pomodoroAdjustButton(systemName: "minus", help: "Remove one pomodoro") {
                    adjustTaskPomodoro(task.id, delta: -1)
                }
                pomodoroAdjustButton(systemName: "plus", help: "Add one pomodoro") {
                    adjustTaskPomodoro(task.id, delta: 1)
                }
            }
            Button {
                acceptSingleTask(task.id)
            } label: {
                // A calendar glyph (not a bare checkmark) so this reads as
                // "push to Apple Calendar", matching the icon on the Sync All
                // buttons. A checkmark here misread as "mark done".
                Image(systemName: isSynced ? "calendar.badge.checkmark" : "calendar.badge.plus")
                    .foregroundStyle(isSynced ? syncedColor : calendarColor)
                    .frame(width: 26, height: 24)
            }
            .buttonStyle(.borderless)
            .disabled(isSynced || isLoadingBackend)
            .help(isSynced ? "Already synced to calendar" : "Sync this task to Calendar")
            // Done toggle (far right = the "✓" the user expects). Feeds the
            // backend completion_store → 复盘/heatmap. Only shown once the task
            // has a backend block to key completion off.
            if blockKey != nil {
                Button {
                    toggleTaskDone(task.id)
                } label: {
                    Image(systemName: isDone ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 15, weight: .semibold))
                        // Same rule: light hollow circle on dark, dark on light;
                        // green fill once done.
                        .foregroundStyle(isDone ? activeColor : controlGlyphColor)
                        .frame(width: 26, height: 24)
                }
                .buttonStyle(.borderless)
                .disabled(isLoadingBackend)
                .help(isDone ? "Mark not done" : "Mark done")
            }
        }
        .padding(9)
        .background {
            ZStack {
                RoundedRectangle(cornerRadius: 11)
                    .fill(upcomingAwareRowFill(isCurrent: isCurrent, isSelected: false))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .contentShape(RoundedRectangle(cornerRadius: 11))
        .overlay {
            RoundedRectangle(cornerRadius: 11)
                .strokeBorder(
                    isSynced && !isCurrent ? syncedColor.opacity(0.30) : isCurrent ? activeColor.opacity(0.22) : Color.clear,
                    lineWidth: 0.75
                )
        }
        .overlay(alignment: .bottomLeading) {
            if isCurrent, let metadata {
                activeProgressLine(start: metadata.start, end: metadata.end)
                    .clipShape(RoundedRectangle(cornerRadius: 11))
            }
        }
        .scaleEffect(isDragging ? 1.01 : 1)
        .opacity(isDragging ? 0.82 : 1)
        .offset(isDragging ? dragOffset : .zero)
        .zIndex(isDragging ? 2 : 0)
        .gesture(
            DragGesture(minimumDistance: 8, coordinateSpace: .named("upcomingTimeline"))
                .onChanged { value in
                    guard !isSynced else { return }
                    beginTimelineDrag(taskID: task.id, location: value.location, translation: value.translation)
                }
                .onEnded { _ in
                    guard !isSynced else { return }
                    endTimelineDrag(taskID: task.id)
                }
        )
        .animation(.easeOut(duration: 0.12), value: draggingTaskID)
        .help(isSynced ? "Synced tasks are fixed" : "Drag this card on the timeline to reflow it")
    }

    private func timelineStamp(start: Date, end: Date, isCurrent: Bool) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(start.formatted(date: .omitted, time: .shortened))
                .font(isCurrent ? .system(size: 13, weight: .semibold) : .caption)
                .foregroundStyle(isCurrent ? activeColor : upcomingSecondaryColor)
            Text(end.formatted(date: .omitted, time: .shortened))
                .font(.caption2)
                .foregroundStyle(upcomingSecondaryColor.opacity(0.72))
        }
        .frame(width: 56, alignment: .leading)
    }

    private func dropBadge(for start: Date) -> some View {
        Text("Drop at \(start.formatted(date: .omitted, time: .shortened))")
            .font(.system(size: 10, weight: .semibold))
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .foregroundStyle(Color.white)
            .background(taskColor.opacity(0.92))
            .clipShape(Capsule())
            .padding(6)
    }

    private func timelineDropIndicator(time: Date) -> some View {
        HStack(spacing: 6) {
            Rectangle()
                .fill(taskColor.opacity(0.82))
                .frame(height: 1.5)
            Text(time.formatted(date: .omitted, time: .shortened))
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Color.white)
                .padding(.horizontal, 7)
                .padding(.vertical, 4)
                .background(taskColor.opacity(0.92))
                .clipShape(Capsule())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .allowsHitTesting(false)
        .shadow(color: taskColor.opacity(0.22), radius: 3, x: 0, y: 1)
        .zIndex(10)
    }

    private func activeProgressLine(start: Date, end: Date) -> some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle()
                    .fill(activeColor.opacity(0.16))
                Rectangle()
                    .fill(activeColor.opacity(0.92))
                    .frame(width: max(3, proxy.size.width * currentProgressFraction(start: start, end: end)))
            }
        }
        .frame(height: 3)
        .frame(maxWidth: .infinity)
        .allowsHitTesting(false)
    }

    private func currentProgressFraction(start: Date, end: Date, now: Date = Date()) -> CGFloat {
        let duration = end.timeIntervalSince(start)
        guard duration > 0 else { return 0 }
        let elapsed = now.timeIntervalSince(start)
        return CGFloat(min(max(elapsed / duration, 0), 1))
    }

    @ViewBuilder
    private var agentProposalModule: some View {
        if let proposal = pendingAgentProposal {
            controlModule(accent: Color(nsColor: .systemOrange)) {
                VStack(alignment: .leading, spacing: 10) {
                    moduleHeader(
                        "Proposed Changes",
                        systemName: "hourglass",
                        caption: "Not applied yet",
                        color: Color(nsColor: .systemOrange)
                    )

                    Text(pendingAgentProposalMessage ?? proposal.summary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)

                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(Array((proposal.changes ?? []).enumerated()), id: \.offset) { _, change in
                            proposalChangeRow(change)
                        }
                    }

                    HStack {
                        Button(isLoadingBackend ? "Applying" : "Apply") {
                            confirmAgentProposal()
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isLoadingBackend)
                        Button("Keep as is") {
                            rejectAgentProposal()
                        }
                        .buttonStyle(.bordered)
                        .disabled(isLoadingBackend)
                    }
                }
            }
        }
    }

    private func proposalChangeRow(_ change: DayflowProposalChange) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(proposalOpLabel(change.op))
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(proposalOpColor(change.op))
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(proposalOpColor(change.op).opacity(0.10))
                .clipShape(Capsule())

            VStack(alignment: .leading, spacing: 2) {
                Text(change.title)
                    .font(.caption.weight(.semibold))
                    .lineLimit(2)
                if let timing = proposalTimingLabel(change) {
                    Text(timing)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color.primary.opacity(0.045))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private var planDraftModule: some View {
        if let draft = state.planDraft {
            controlModule {
                VStack(alignment: .leading, spacing: 10) {
                    moduleHeader("Plan Draft", systemName: "calendar.badge.clock", caption: draft.summary)

                    ForEach(draft.blocks) { block in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(block.taskTitle)
                                    .font(.system(size: 14, weight: .semibold))
                                    .lineLimit(1)
                                Spacer()
                                Text(timeRange(block.start, block.end))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Text(block.rationale)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        .padding(9)
                        .background(Color.primary.opacity(0.045))
                        .clipShape(RoundedRectangle(cornerRadius: 11))
                        .overlay {
                            RoundedRectangle(cornerRadius: 11)
                                .strokeBorder(Color.primary.opacity(0.07), lineWidth: 0.5)
                        }
                        .draggable(block.taskID.uuidString)
                    }

                    draftDropTimeline

                    if let risk = draft.risk {
                        Label(risk, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }

                    HStack {
                        Button("Confirm") {
                            state.confirmDraft()
                        }
                        .buttonStyle(.borderedProminent)
                        Button("Dismiss") {
                            state.dismissDraft()
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
        }
    }

    private var draftDropTimeline: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Drag a draft block to adjust time")
                .font(.caption2)
                .foregroundStyle(.secondary)
            HStack(spacing: 6) {
                ForEach([9, 11, 14, 16], id: \.self) { hour in
                    Text(hourLabel(hour))
                        .font(.caption)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 7)
                        .background(Color.primary.opacity(0.045))
                        .clipShape(RoundedRectangle(cornerRadius: 9))
                        .overlay {
                            RoundedRectangle(cornerRadius: 9)
                                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5)
                        }
                        .dropDestination(for: String.self) { items, _ in
                            guard let raw = items.first,
                                  let taskID = UUID(uuidString: raw)
                            else {
                                return false
                            }
                            pinTask(taskID, toStart: dateAtHour(hour))
                            return true
                        }
                }
            }
        }
    }

    private var upcomingModule: some View {
        let entries = state.upcomingLaneEntries(now: Date())
        let scheduledCount = entries.filter { $0.kind != .dropSlot }.count

        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                HStack(spacing: 7) {
                    Image(systemName: "clock")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(upcomingAccentColor)
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Upcoming")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(upcomingPrimaryColor)
                        Text("\(scheduledCount) scheduled")
                            .font(.caption)
                            .foregroundStyle(upcomingSecondaryColor)
                            .lineLimit(1)
                    }
                }
                Spacer()
                Button {
                    regenerateToday()
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(upcomingPrimaryColor)
                        .frame(width: 28, height: 26)
                        .background(upcomingRowFill)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(isLoadingBackend)
                .help("重新生成今天（按最新日历和睡眠）")
                Button {
                    syncQueueToCalendar()
                } label: {
                    Label(isLoadingBackend ? "Syncing" : "Sync All", systemImage: "calendar.badge.plus")
                }
                .buttonStyle(.bordered)
                .disabled(state.todayQueue.isEmpty || isLoadingBackend)
                Button {
                    calendarAdapter.openInCalendar(near: Date())
                } label: {
                    Image(systemName: "arrow.up.forward.app")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(upcomingPrimaryColor)
                        .frame(width: 28, height: 26)
                        .background(upcomingRowFill)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .help("Open Calendar")
            }

            if entries.isEmpty {
                if isLoadingBackend {
                    // Generation in flight — don't look like an empty day.
                    GeneratingPlaceholderView(
                        rowFill: upcomingRowFill, textColor: upcomingSecondaryColor)
                } else {
                    Text("Nothing upcoming.")
                        .font(.callout)
                        .foregroundStyle(upcomingSecondaryColor)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(9)
                        .background(upcomingRowFill)
                        .clipShape(RoundedRectangle(cornerRadius: 11))
                }
            } else {
                VStack(spacing: 7) {
                    ForEach(entries) { entry in
                        upcomingLaneRow(entry)
                    }
                }
                .coordinateSpace(name: "upcomingTimeline")
                .background {
                    GeometryReader { proxy in
                        Color.clear.preference(key: TimelineHeightPreferenceKey.self, value: proxy.size.height)
                    }
                }
                .onPreferenceChange(TimelineHeightPreferenceKey.self) { height in
                    upcomingTimelineHeight = height
                }
                .overlay(alignment: .topLeading) {
                    if draggingTaskID != nil,
                       let dragDropY,
                       let dragDropTime {
                        timelineDropIndicator(time: dragDropTime)
                            .offset(y: dragDropY)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: cardCornerRadius)
                .fill(upcomingModuleFill)
                .shadow(color: .black.opacity(cardShadowOpacity), radius: cardShadowRadius, x: 0, y: cardShadowYOffset)
        }
        .overlay {
            RoundedRectangle(cornerRadius: cardCornerRadius)
                .strokeBorder(Color.primary.opacity(cardStrokeOpacity), lineWidth: cardStrokeWidth)
        }
    }

    @ViewBuilder
    private func upcomingLaneRow(_ entry: UpcomingLaneEntry) -> some View {
        switch entry.kind {
        case .agentTask:
            if let task = entry.task {
                queueTaskRow(task)
            }
        case .calendar:
            upcomingCalendarRow(entry)
        case .dropSlot:
            upcomingDropSlotRow(entry)
        }
    }

    private func upcomingCalendarRow(_ entry: UpcomingLaneEntry) -> some View {
        let isSelected = selectedEventID == entry.sourceID

        return Button {
            selectedEventID = entry.sourceID
            if let event = entry.event {
                calendarAdapter.openInCalendar(near: event.start)
            }
        } label: {
            HStack(spacing: 10) {
                timelineStamp(start: entry.start, end: entry.end, isCurrent: entry.isInProgress)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(entry.title)
                            .font(.system(size: entry.isInProgress ? 15 : 14, weight: entry.isInProgress ? .semibold : .regular))
                            .foregroundStyle(entry.isInProgress ? activeColor : upcomingPrimaryColor)
                            .lineLimit(1)
                    }
                    Text(entry.isInProgress ? "in progress · until \(entry.end.formatted(date: .omitted, time: .shortened))" : "calendar anchor")
                        .font(.caption)
                        .foregroundStyle(entry.isInProgress ? activeColor : upcomingSecondaryColor)
                }
                Spacer()
            }
            .padding(entry.isInProgress ? 10 : 8)
            .background {
                RoundedRectangle(cornerRadius: entry.isInProgress ? 12 : 11)
                    .fill(upcomingAwareRowFill(isCurrent: entry.isInProgress, isSelected: isSelected))
            }
            .clipShape(RoundedRectangle(cornerRadius: entry.isInProgress ? 12 : 11))
            .overlay {
                RoundedRectangle(cornerRadius: entry.isInProgress ? 12 : 11)
                    .strokeBorder(
                        entry.isInProgress ? activeColor.opacity(0.22) : Color.clear,
                        lineWidth: 0.75
                    )
            }
            .overlay(alignment: .bottomLeading) {
                if entry.isInProgress {
                    activeProgressLine(start: entry.start, end: entry.end)
                        .clipShape(RoundedRectangle(cornerRadius: entry.isInProgress ? 12 : 11))
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func upcomingDropSlotRow(_ entry: UpcomingLaneEntry) -> some View {
        return HStack(spacing: 10) {
            Text(entry.start.formatted(date: .omitted, time: .shortened))
                .font(.caption2)
                .foregroundStyle(upcomingSecondaryColor.opacity(0.70))
                .frame(width: 56, alignment: .leading)
            Rectangle()
                .fill(upcomingSecondaryColor.opacity(0.18))
                .frame(height: 1)
            Text("available")
                .font(.caption2)
                .foregroundStyle(upcomingSecondaryColor.opacity(0.62))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background {
            RoundedRectangle(cornerRadius: 9)
                .fill(Color.clear)
        }
        .clipShape(RoundedRectangle(cornerRadius: 9))
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            // Spinner while a backend request is in flight (agent chat, generate,
            // sync) so a request that takes a few seconds shows visible progress
            // instead of a frozen line of text; static dot when idle.
            if isLoadingBackend {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 12, height: 12)
            } else {
                Circle()
                    .fill(activeColor.opacity(0.82))
                    .frame(width: 7, height: 7)
            }
            Text(state.statusMessage)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .background {
            RoundedRectangle(cornerRadius: 14)
                .fill(moduleFill)
                .shadow(color: .black.opacity(cardShadowOpacity), radius: cardShadowRadius, x: 0, y: cardShadowYOffset)
        }
        .overlay {
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(Color.primary.opacity(0.24), lineWidth: 0.75)
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 10)
    }

    private var moduleFill: Color {
        Color(nsColor: .textBackgroundColor).opacity(0.99)
    }

    private var rowFill: some ShapeStyle {
        Color(nsColor: .windowBackgroundColor).opacity(0.36)
    }

    private var upcomingModuleFill: Color {
        colorScheme == .dark ? Color.white.opacity(0.94) : Color.black.opacity(0.90)
    }

    private var upcomingPrimaryColor: Color {
        colorScheme == .dark ? Color.black.opacity(0.88) : Color.white.opacity(0.94)
    }

    private var upcomingSecondaryColor: Color {
        colorScheme == .dark ? Color.black.opacity(0.58) : Color.white.opacity(0.62)
    }

    private var upcomingAccentColor: Color {
        colorScheme == .dark ? Color(nsColor: .systemRed) : Color(nsColor: .systemRed).opacity(0.92)
    }

    private var upcomingRowFill: Color {
        colorScheme == .dark ? Color.black.opacity(0.06) : Color.white.opacity(0.10)
    }

    private func upcomingAwareRowFill(isCurrent: Bool, isSelected: Bool) -> Color {
        if isCurrent {
            return upcomingRowFill
        }
        if isSelected {
            return upcomingAccentColor.opacity(colorScheme == .dark ? 0.10 : 0.18)
        }
        return upcomingRowFill
    }

    private func currentAwareRowFill(isCurrent: Bool, isSelected: Bool) -> some ShapeStyle {
        if isCurrent {
            return activeColor.opacity(0.075)
        }
        if isSelected {
            return calendarColor.opacity(0.055)
        }
        return Color(nsColor: .windowBackgroundColor).opacity(0.36)
    }

    private func controlModule<Content: View>(accent: Color? = nil, @ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: cardCornerRadius)
                    .fill(moduleFill)
                    .shadow(color: .black.opacity(cardShadowOpacity), radius: cardShadowRadius, x: 0, y: cardShadowYOffset)
            }
            .overlay {
                RoundedRectangle(cornerRadius: cardCornerRadius)
                    .strokeBorder(Color.primary.opacity(cardStrokeOpacity), lineWidth: cardStrokeWidth)
            }
    }

    private func controlTile(title: String, subtitle: String, systemName: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(color.opacity(0.09))
                    Image(systemName: systemName)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(color)
                }
                .frame(width: 32, height: 32)

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: 13, weight: .semibold))
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(10)
            .frame(maxWidth: .infinity, minHeight: 62, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: cardCornerRadius)
                    .fill(moduleFill)
                    .shadow(color: .black.opacity(cardShadowOpacity), radius: cardShadowRadius, x: 0, y: cardShadowYOffset)
            }
            .overlay {
                RoundedRectangle(cornerRadius: cardCornerRadius)
                    .strokeBorder(Color.primary.opacity(cardStrokeOpacity), lineWidth: cardStrokeWidth)
            }
        }
        .buttonStyle(.plain)
    }

    private func moduleHeader(_ title: String, systemName: String, caption: String, color: Color = .secondary) -> some View {
        HStack(spacing: 7) {
            Image(systemName: systemName)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(color)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.system(size: 13, weight: .semibold))
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
    }

    private func iconButton(systemName: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 13, weight: .semibold))
                .frame(width: 28, height: 26)
                .background(Color(nsColor: .windowBackgroundColor).opacity(0.36))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private func pomodoroAdjustButton(systemName: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 11, weight: .semibold))
                // Contrast comes from the glyph, not a heavy box: light glyph on
                // the dark night background, dark glyph on the light day one.
                .foregroundStyle(controlGlyphColor)
                .frame(width: 20, height: 20)
                .background {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.primary.opacity(0.07))
                }
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private func capsuleLabel(_ text: String) -> some View {
        capsuleLabel(text, color: Color(nsColor: .secondaryLabelColor))
    }

    private func capsuleLabel(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .foregroundStyle(color)
            .background(color.opacity(0.12))
            .clipShape(Capsule())
    }

    private func priorityBadge(_ priority: TaskPriority, prominent: Bool) -> some View {
        let label = priorityLabel(priority).uppercased()
        let color = priorityColor(priority)

        return Text(label)
            .font(.system(size: prominent ? 11 : 9, weight: .bold))
            .padding(.horizontal, prominent ? 8 : 6)
            .padding(.vertical, prominent ? 4 : 3)
            .foregroundStyle(prominent ? Color.white : color)
            .background {
                Capsule()
                    .fill(prominent ? color : color.opacity(0.12))
            }
            .overlay {
                Capsule()
                    .strokeBorder(color.opacity(prominent ? 0 : 0.30), lineWidth: 0.75)
            }
    }

    private func backendTaskBadge(_ label: String, cognitiveLoad: DayflowCognitiveLoad?) -> some View {
        let color = backendTaskBadgeColor(cognitiveLoad)

        return Text(label)
            .font(.system(size: 8, weight: .bold))
            .lineLimit(1)
            .padding(.horizontal, 5)
            .padding(.vertical, 2.5)
            .foregroundStyle(color)
            .background {
                Capsule()
                    .fill(color.opacity(colorScheme == .dark ? 0.10 : 0.16))
            }
            .overlay {
                Capsule()
                    .strokeBorder(color.opacity(0.32), lineWidth: 0.7)
            }
            // The pill packs two dimensions: the text is the task TYPE
            // (analytical / insight / admin) and the COLOR is the focus
            // intensity. Spell that out on hover so the shades aren't a mystery.
            .help("\(label) · \(cognitiveLoadHelp(cognitiveLoad))")
    }

    private func cognitiveLoadHelp(_ cognitiveLoad: DayflowCognitiveLoad?) -> String {
        switch cognitiveLoad {
        case .deep:   "deep focus (blue)"
        case .medium: "medium focus (teal)"
        case .light:  "light effort (grey)"
        case nil:     "unspecified effort"
        }
    }

    private func backendTaskBadgeColor(_ cognitiveLoad: DayflowCognitiveLoad?) -> Color {
        switch cognitiveLoad {
        case .deep:
            Color(nsColor: .systemBlue)
        case .medium:
            Color(nsColor: .systemTeal)
        case .light:
            quietColor
        case nil:
            quietColor
        }
    }

    private func emptyText(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(9)
            .background(rowFill)
            .clipShape(RoundedRectangle(cornerRadius: 11))
    }

    private func parseCommand() {
        let message = commandText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty else { return }
        commandText = ""
        Task {
            await sendAgentMessage(message)
        }
    }

    private func prepareCommandInput() {
        SidebarWindowController.shared.prepareForTextInput()
        DispatchQueue.main.async {
            commandFocused = true
        }
    }

    private func loadDayflowSchedule(generate: Bool) {
        guard !isLoadingBackend else { return }
        isLoadingBackend = true
        state.statusMessage = generate ? "Loading Dayflow agent schedule..." : "Refreshing Dayflow schedule..."
        Task {
            do {
                let calendarEvents = generate ? localCalendarEventsForCurrentDate() : nil
                let fixedMinutes = generate ? futureCapacityForPlanning() : nil
                let schedule = try await (generate
                    ? dayflowClient.generateSchedule(date: scheduleDate, calendarEvents: calendarEvents,
                                                     fixedMinutesByDate: fixedMinutes)
                    : dayflowClient.fetchSchedule(date: scheduleDate))
                await MainActor.run {
                    state.applyDayflowSchedule(schedule, now: Date())
                    isLoadingBackend = false
                }
                await refreshHealthSnapshot()
            } catch {
                await MainActor.run {
                    state.clearForBackendError("Dayflow backend unavailable: \(error.localizedDescription)")
                    isLoadingBackend = false
                }
            }
        }
    }

    /// Startup: reuse today's cached schedule if the backend already has one (a
    /// fast GET, no LLM / CalDAV / regeneration). Only generate via the stream
    /// when there's no schedule for today yet (first open of the day) — matching
    /// the "don't re-run the LLM when nothing changed" design. Explicit
    /// regeneration still goes through loadDayflowSchedule(generate:true).
    private func loadTodaySchedulePreferringCache() {
        guard !isLoadingBackend else { return }
        isLoadingBackend = true
        Task {
            do {
                let schedule = try await dayflowClient.fetchSchedule(date: scheduleDate)
                await MainActor.run {
                    state.applyDayflowSchedule(schedule, now: Date())
                    isLoadingBackend = false
                }
                await refreshHealthSnapshot()
            } catch {
                // No cached schedule for today → generate it once via the stream.
                await MainActor.run { isLoadingBackend = false }
                startDayflowStream()
            }
        }
    }

    /// Read the day's local calendar (requesting event access) so generation
    /// schedules around the user's real events via EventKit instead of CalDAV.
    /// Returns nil if access is denied or the date can't be parsed — callers then
    /// fall back to the no-calendar path (backend reads CalDAV).
    private func localCalendarEventsForCurrentDate() -> [DayflowCalendarEventInput]? {
        // Only read when access is ALREADY granted — never prompt here, or a
        // pending permission dialog would stall the schedule (and its energy
        // curve). The prompt is fired once in onAppear.
        guard calendarAdapter.hasEventAccess else { return nil }
        guard let day = Self.date(from: scheduleDate) else { return nil }
        return calendarAdapter.localCalendarEvents(on: day)
    }

    /// Committed minutes per day over the planning horizon, read from the local
    /// calendar — lets the backend auto-plan multi-day project work around real
    /// commitments. nil (no prompt) when access isn't granted yet.
    private func futureCapacityForPlanning() -> [String: Int]? {
        guard calendarAdapter.hasEventAccess else { return nil }
        return calendarAdapter.fixedMinutesByDate(from: Date(), days: 30)
    }

    private func startDayflowStream() {
        guard streamTask == nil else { return }
        isLoadingBackend = true
        state.beginDayflowStream()
        streamTask = Task {
            // Upload the local calendar so the backend works around real events.
            let calendarEvents = localCalendarEventsForCurrentDate()
            let fixedMinutes = futureCapacityForPlanning()
            do {
                for try await event in dayflowClient.streamSchedule(
                    date: scheduleDate, calendarEvents: calendarEvents,
                    fixedMinutesByDate: fixedMinutes) {
                    await MainActor.run {
                        switch event {
                        case let .health(energyCurve, healthSummary, energySource):
                            state.applyDayflowHealth(
                                energyCurve: energyCurve,
                                healthSummary: healthSummary,
                                energySource: energySource
                            )
                            Task {
                                await refreshHealthSnapshot()
                            }
                        case let .fixed(blocks):
                            state.applyDayflowFixedBlocks(blocks, now: Date())
                        case let .schedule(blocks, unscheduled):
                            state.applyDayflowScheduleBlocks(
                                date: scheduleDate,
                                blocks: blocks,
                                unscheduled: unscheduled,
                                now: Date()
                            )
                            Task {
                                await refreshHealthSnapshot()
                            }
                            isLoadingBackend = false
                        case .done:
                            isLoadingBackend = false
                            streamTask = nil
                        case let .error(message):
                            isLoadingBackend = false
                            streamTask = nil
                            Task { await recoverWithCachedSchedule(
                                reason: "Dayflow stream failed: \(message)") }
                        }
                    }
                }
                await MainActor.run {
                    isLoadingBackend = false
                    streamTask = nil
                }
            } catch {
                await MainActor.run {
                    isLoadingBackend = false
                    streamTask = nil
                }
                // A stream hiccup (e.g. backend reload) shouldn't wipe the energy
                // curve — fall back to the last cached schedule, which carries it.
                await recoverWithCachedSchedule(
                    reason: "Dayflow stream unavailable: \(error.localizedDescription)")
            }
        }
    }

    private func syncQueueToCalendar() {
        guard !isLoadingBackend else { return }
        let blockKeys = state.todayQueue.compactMap { state.backendBlockKey(for: $0.id) }
        isLoadingBackend = true
        state.statusMessage = "Writing Dayflow schedule to calendar..."
        Task {
            do {
                let response = try await dayflowClient.writeSchedule(date: scheduleDate)
                await MainActor.run {
                    for blockKey in blockKeys {
                        state.markBackendBlockSynced(blockKey)
                    }
                    state.statusMessage = "Backend wrote \(response.written) calendar block\(response.written == 1 ? "" : "s")."
                    isLoadingBackend = false
                }
                await reloadAfterBackendMutation()
            } catch {
                await MainActor.run {
                    state.statusMessage = "Backend calendar write failed: \(error.localizedDescription)"
                    isLoadingBackend = false
                }
            }
        }
    }

    private func acceptSingleTask(_ taskID: UUID) {
        guard let start = state.backendStart(for: taskID),
              let blockKey = state.backendBlockKey(for: taskID)
        else {
            state.statusMessage = "This task has no backend schedule block yet."
            return
        }
        guard !isLoadingBackend else { return }
        isLoadingBackend = true
        state.statusMessage = "Writing one Dayflow block to calendar..."
        Task {
            do {
                let response = try await dayflowClient.writeScheduleBlock(date: scheduleDate, start: start)
                await MainActor.run {
                    state.markBackendBlockSynced(blockKey)
                    state.statusMessage = response.skipped == true
                        ? "Backend skipped this block because it is already synced."
                        : "Backend wrote this block to calendar."
                    isLoadingBackend = false
                }
                await reloadAfterBackendMutation()
            } catch {
                await MainActor.run {
                    state.statusMessage = "Single block write failed: \(error.localizedDescription)"
                    isLoadingBackend = false
                }
            }
        }
    }

    /// Toggle a task's done state. Optimistic (the checkmark flips instantly),
    /// then persisted to the backend completion_store, which feeds the
    /// 复盘/heatmap. Reverts on failure. Deliberately does NOT set
    /// isLoadingBackend — marking done shouldn't lock the whole panel.
    private func toggleTaskDone(_ taskID: UUID) {
        guard let blockKey = state.backendBlockKey(for: taskID), !isLoadingBackend else { return }
        let newValue = !state.isTaskDone(taskID)
        state.setTaskDone(taskID, done: newValue)
        Task {
            do {
                _ = try await dayflowClient.setBlockCompletion(
                    date: scheduleDate, blockKey: blockKey, done: newValue)
            } catch {
                await MainActor.run {
                    state.setTaskDone(taskID, done: !newValue)  // revert
                    state.statusMessage = "Mark done failed: \(error.localizedDescription)"
                }
            }
        }
    }

    private func pinTask(_ taskID: UUID, toStart start: Date) {
        guard let blockKey = state.backendBlockKey(for: taskID),
              let metadata = state.backendScheduleMetadata(for: taskID)
        else {
            state.statusMessage = "This task cannot be pinned until it comes from Dayflow schedule."
            return
        }

        let duration = max(5, Int(metadata.end.timeIntervalSince(metadata.start) / 60))
        state.statusMessage = "Asking Dayflow to reflow this task..."
        Task {
            do {
                let response = try await dayflowClient.pinBlock(
                    date: scheduleDate,
                    blockKey: blockKey,
                    start: start,
                    durationMinutes: duration
                )
                await MainActor.run {
                    state.applyDayflowSchedule(response.schedule, now: Date())
                state.statusMessage = response.adjusted
                    ? "Dayflow moved it to the nearest available slot."
                    : "Dayflow pinned and reflowed the schedule."
                }
                await refreshHealthSnapshot()
            } catch {
                await MainActor.run {
                    state.statusMessage = "Pin failed: \(error.localizedDescription)"
                }
            }
        }
    }

    private func adjustTaskPomodoro(_ taskID: UUID, delta: Int) {
        guard let blockKey = state.backendBlockKey(for: taskID),
              let metadata = state.backendScheduleMetadata(for: taskID)
        else {
            state.statusMessage = "This task cannot be resized until it comes from Dayflow schedule."
            return
        }

        let nextDuration = metadata.durationMinutes(adjustingPomodoroCountBy: delta)
        state.adjustBackendScheduleMetadata(for: taskID, durationMinutes: nextDuration, pomodoroDelta: delta)
        state.statusMessage = delta > 0 ? "Added one pomodoro locally." : "Removed one pomodoro locally."

        resizeDebounceTasks[taskID]?.cancel()
        resizeDebounceTasks[taskID] = Task {
            do {
                try await Task.sleep(nanoseconds: 500_000_000)
                guard !Task.isCancelled else { return }
            } catch {
                return
            }

            do {
                let response = try await dayflowClient.pinBlock(
                    date: scheduleDate,
                    blockKey: blockKey,
                    start: metadata.start,
                    durationMinutes: nextDuration
                )
                await MainActor.run {
                    state.applyDayflowSchedule(response.schedule, now: Date())
                    state.statusMessage = response.adjusted
                        ? "Dayflow resized it and moved it to the nearest available slot."
                        : "Dayflow resized this task and reflowed the day."
                    resizeDebounceTasks[taskID] = nil
                }
                await refreshHealthSnapshot()
            } catch {
                await MainActor.run {
                    state.statusMessage = "Resize failed: \(error.localizedDescription)"
                    resizeDebounceTasks[taskID] = nil
                }
            }
        }
    }

    private func beginTimelineDrag(taskID: UUID, location: CGPoint, translation: CGSize) {
        guard upcomingTimelineHeight > 0 else { return }
        draggingTaskID = taskID
        dragOffset = translation
        let clampedY = min(max(location.y, 0), upcomingTimelineHeight)
        let mapper = ScheduleTimelineDropMapper(
            targetDate: scheduleDate,
            workStartHour: 8,
            workEndHour: 22,
            snapMinutes: 15
        )
        dragDropY = clampedY
        dragDropTime = mapper.date(forY: clampedY, inHeight: upcomingTimelineHeight)
    }

    private func endTimelineDrag(taskID: UUID) {
        defer {
            draggingTaskID = nil
            dragTargetTaskID = nil
            dragOffset = .zero
            dragDropTime = nil
            dragDropY = nil
        }

        guard let dragDropTime else { return }
        pinTask(taskID, toStart: dragDropTime)
    }

    private func sendAgentMessage(_ message: String) async {
        await MainActor.run {
            isLoadingBackend = true
            state.statusMessage = "Asking Dayflow agent..."
        }
        do {
            let result = try await dayflowClient.sendAgentMessage(date: scheduleDate, message: message)
            let shouldReload = agentChatRefreshPolicy.shouldReloadSchedule(terminalState: result.terminalState)
            let shouldPresentProposal = agentChatRefreshPolicy.shouldPresentProposal(terminalState: result.terminalState)
            await MainActor.run {
                if shouldPresentProposal, let proposal = result.proposal {
                    pendingAgentProposal = proposal
                    pendingAgentProposalMessage = result.message
                } else if let schedule = result.schedule {
                    pendingAgentProposal = nil
                    pendingAgentProposalMessage = nil
                    state.applyDayflowSchedule(schedule, now: Date())
                }
                state.statusMessage = result.message
            }
            if shouldReload {
                await MainActor.run {
                    pendingAgentProposal = nil
                    pendingAgentProposalMessage = nil
                }
                await reloadScheduleAfterAgentSuccess(statusMessage: result.message)
            } else {
                await MainActor.run {
                    isLoadingBackend = false
                }
            }
        } catch {
            await MainActor.run {
                state.statusMessage = "Agent request failed: \(error.localizedDescription)"
                isLoadingBackend = false
            }
        }
    }

    private func confirmAgentProposal() {
        guard pendingAgentProposal != nil, !isLoadingBackend else { return }
        isLoadingBackend = true
        state.statusMessage = "Applying proposed changes..."
        Task {
            do {
                let result = try await dayflowClient.confirmAgentProposal(date: scheduleDate)
                if agentChatRefreshPolicy.shouldReloadSchedule(terminalState: result.terminalState) {
                    await MainActor.run {
                        pendingAgentProposal = nil
                        pendingAgentProposalMessage = nil
                    }
                    await reloadScheduleAfterAgentSuccess(statusMessage: result.message)
                } else {
                    // Non-success (expired / superseded / schedule changed): the
                    // backend has already discarded this proposal, so keeping the
                    // card would leave an Apply button that silently does nothing.
                    // Clear it, surface WHY, and show the latest schedule if the
                    // backend sent one back.
                    await MainActor.run {
                        pendingAgentProposal = nil
                        pendingAgentProposalMessage = nil
                        if let schedule = result.schedule {
                            state.applyDayflowSchedule(schedule, now: Date())
                        }
                        state.statusMessage = result.message
                        isLoadingBackend = false
                    }
                }
            } catch {
                await MainActor.run {
                    state.statusMessage = "Apply failed: \(error.localizedDescription)"
                    isLoadingBackend = false
                }
            }
        }
    }

    private func rejectAgentProposal() {
        pendingAgentProposal = nil
        pendingAgentProposalMessage = nil
        state.statusMessage = "Kept schedule unchanged."
    }

    private func reloadScheduleAfterAgentSuccess(statusMessage: String) async {
        do {
            let schedule = try await dayflowClient.fetchSchedule(date: scheduleDate)
            await MainActor.run {
                pendingAgentProposal = nil
                pendingAgentProposalMessage = nil
                state.applyDayflowSchedule(schedule, now: Date())
                state.statusMessage = statusMessage
                isLoadingBackend = false
            }
            await refreshHealthSnapshot()
        } catch {
            await MainActor.run {
                state.statusMessage = "\(statusMessage) Refresh failed: \(error.localizedDescription)"
                isLoadingBackend = false
            }
        }
    }

    private func reloadAfterBackendMutation() async {
        do {
            let schedule = try await dayflowClient.fetchSchedule(date: scheduleDate)
            await MainActor.run {
                state.applyDayflowSchedule(schedule, now: Date())
            }
            await refreshHealthSnapshot()
        } catch {
            await MainActor.run {
                state.statusMessage += " Refresh failed: \(error.localizedDescription)"
            }
        }
    }

    /// The energy curve only arrives via the generate stream; if that stream
    /// fails, recover it from the last cached schedule (which includes the curve
    /// + energy source) so we never end up with sleep data but a blank curve.
    private func recoverWithCachedSchedule(reason: String) async {
        do {
            let schedule = try await dayflowClient.fetchSchedule(date: scheduleDate)
            await MainActor.run { state.applyDayflowSchedule(schedule, now: Date()) }
            await refreshHealthSnapshot()
        } catch {
            await MainActor.run { state.statusMessage = reason }
        }
    }

    private func refreshHealthSnapshot() async {
        do {
            let snapshot = try await dayflowClient.fetchHealthSnapshot(date: scheduleDate)
            await MainActor.run {
                state.applyHealthSnapshot(
                    sleepStart: snapshot.sleep.sleepStart,
                    sleepEnd: snapshot.sleep.sleepEnd,
                    restingHeartRate: snapshot.restingHeartRate,
                    hrv: snapshot.hrv.map { Int($0.rounded()) },
                    steps: snapshot.steps
                )
            }
        } catch {
            // Health details are optional; the energy curve can still render from the stream.
        }
    }

    private func submitManualSleepWindow(sleepStart: Date, sleepEnd: Date) {
        let targetDate = scheduleDate
        guard let normalized = ManualSleepWindowNormalizer.normalized(
            sleepStart: sleepStart,
            sleepEnd: sleepEnd,
            targetDate: targetDate
        ) else {
            state.statusMessage = "Sleep input save failed: invalid date."
            return
        }
        let restingHeartRate = state.healthSignal.restingHeartRate
        let hrv = state.healthSignal.hrv.map(Double.init)
        let steps = state.healthSignal.steps
        state.applyManualSleepWindow(sleepStart: normalized.start, sleepEnd: normalized.end)
        state.statusMessage = "Saving sleep input to Dayflow..."
        isLoadingBackend = true

        Task {
            do {
                let snapshot = try await dayflowClient.updateHealthSnapshot(
                    date: targetDate,
                    sleepStart: normalized.start,
                    sleepEnd: normalized.end,
                    restingHeartRate: restingHeartRate,
                    hrv: hrv,
                    steps: steps
                )
                await MainActor.run {
                    state.applyHealthSnapshot(
                        sleepStart: snapshot.sleep.sleepStart,
                        sleepEnd: snapshot.sleep.sleepEnd,
                        restingHeartRate: snapshot.restingHeartRate,
                        hrv: snapshot.hrv.map { Int($0.rounded()) },
                        steps: snapshot.steps
                    )
                    state.statusMessage = "Sleep input saved. Regenerating schedule..."
                    isLoadingBackend = false
                }
                await MainActor.run {
                    loadDayflowSchedule(generate: true)
                }
            } catch {
                await MainActor.run {
                    state.statusMessage = "Sleep input save failed: \(error.localizedDescription)"
                    isLoadingBackend = false
                }
            }
        }
    }

    private func taskDurationLabel(_ task: TaskItem) -> String {
        task.estimatedMinutes.map { "\($0)m" } ?? "needs duration"
    }

    private func taskDeadlineLabel(_ task: TaskItem) -> String {
        task.deadline.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "needs deadline"
    }

    private func taskTimingLabel(_ task: TaskItem) -> String {
        guard let metadata = state.backendScheduleMetadata(for: task.id) else {
            return "\(taskDurationLabel(task)) · \(taskDeadlineLabel(task))"
        }
        return pomodoroLabel(metadata)
    }

    private func pomodoroLabel(_ metadata: DayflowTaskScheduleMetadata) -> String {
        metadata.pomodoroSessionLabel
    }

    private func priorityLabel(_ priority: TaskPriority) -> String {
        switch priority {
        case .low: "low"
        case .medium: "medium"
        case .high: "high"
        case .urgent: "urgent"
        }
    }

    private func proposalOpLabel(_ op: String) -> String {
        switch op {
        case "move": "Move"
        case "remove": "Remove"
        case "add": "Add"
        default: op.capitalized
        }
    }

    private func proposalOpColor(_ op: String) -> Color {
        switch op {
        case "remove": Color(nsColor: .systemRed)
        case "add": Color(nsColor: .systemGreen)
        default: Color(nsColor: .systemBlue)
        }
    }

    private func proposalTimingLabel(_ change: DayflowProposalChange) -> String? {
        switch change.op {
        case "move":
            if let from = change.fromTime, let to = change.toTime {
                return "\(from) -> \(to)"
            }
        case "remove":
            return change.fromTime
        case "add":
            return change.toTime
        default:
            return change.toTime ?? change.fromTime
        }
        return change.toTime ?? change.fromTime
    }

    private func priorityColor(_ priority: TaskPriority) -> Color {
        switch priority {
        case .low:
            Color(nsColor: .tertiaryLabelColor)
        case .medium:
            taskColor
        case .high:
            quietColor
        case .urgent:
            Color(nsColor: .systemRed)
        }
    }

    private func timeRange(_ start: Date, _ end: Date) -> String {
        "\(start.formatted(date: .omitted, time: .shortened))-\(end.formatted(date: .omitted, time: .shortened))"
    }

    private func hourLabel(_ hour: Int) -> String {
        let suffix = hour < 12 ? "a" : "p"
        let display = hour <= 12 ? hour : hour - 12
        return "\(display)\(suffix)"
    }

    private func dateAtHour(_ hour: Int) -> Date {
        Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: Date()) ?? Date()
    }

    private func routeIcon(_ destination: DocumentRouteDestination) -> String {
        switch destination {
        case .todayQueue: "tray.full"
        case .weeklyPlan: "calendar"
        case .memory: "brain.head.profile"
        case .clarification: "questionmark.bubble"
        }
    }

    private func routeColor(_ destination: DocumentRouteDestination) -> Color {
        switch destination {
        case .todayQueue: taskColor
        case .weeklyPlan: calendarColor
        case .memory: activeColor
        case .clarification: quietColor
        }
    }

    private static func todayString() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: Date())
    }

    /// Parse a "yyyy-MM-dd" schedule date to a local Date (used to read that day's
    /// calendar for generation).
    private static func date(from string: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: string)
    }
}

/// Shown in the Upcoming lane while the backend is computing a schedule, so an
/// in-flight generation doesn't read as an empty day. A spinner line + a few
/// gently pulsing skeleton rows.
private struct GeneratingPlaceholderView: View {
    let rowFill: Color
    let textColor: Color
    @State private var pulse = false

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("正在为你计算今天的日程…")
                    .font(.callout)
                    .foregroundStyle(textColor)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(9)
            .background(rowFill)
            .clipShape(RoundedRectangle(cornerRadius: 11))

            ForEach(0..<3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 11)
                    .fill(rowFill)
                    .frame(height: 44)
                    .opacity(pulse ? 0.45 : 0.95)
            }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }
}

private struct EnergyCurveView: View {
    var values: [Double]
    var accent: Color

    var body: some View {
        GeometryReader { proxy in
            let width = max(proxy.size.width, 1)
            let height = max(proxy.size.height, 1)
            let axisY = max(height - 17, 28)
            let progress = currentDayProgress()
            let playheadX = min(max(width * progress, 0), width - 1)

            ZStack(alignment: .leading) {
                Path { path in
                    let points = points(in: CGSize(width: width, height: axisY))
                    guard let first = points.first else { return }
                    path.move(to: first)
                    for index in points.indices.dropFirst() {
                        let previousControl = points[max(index - 2, points.startIndex)]
                        let previous = points[index - 1]
                        let current = points[index]
                        let next = points[min(index + 1, points.index(before: points.endIndex))]
                        let tension: CGFloat = 0.28
                        path.addCurve(
                            to: current,
                            control1: CGPoint(
                                x: previous.x + (current.x - previousControl.x) * tension,
                                y: previous.y + (current.y - previousControl.y) * tension
                            ),
                            control2: CGPoint(
                                x: current.x - (next.x - previous.x) * tension,
                                y: current.y - (next.y - previous.y) * tension
                            )
                        )
                    }
                }
                .stroke(accent.opacity(0.82), style: StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))

                Rectangle()
                    .fill(Color.primary.opacity(0.10))
                    .frame(height: 1)
                    .offset(y: axisY)

                ForEach(timeTicks, id: \.label) { tick in
                    VStack(spacing: 1) {
                        Rectangle()
                            .fill(Color.primary.opacity(0.16))
                            .frame(width: 1, height: 4)
                        Text(tick.label)
                            .font(.system(size: 8.5, weight: .medium))
                            .foregroundStyle(.secondary)
                    }
                    .frame(width: 28)
                    .position(x: width * tick.position, y: axisY + 7.5)
                }

                ZStack(alignment: .top) {
                    Rectangle()
                        .fill(Color.primary.opacity(0.34))
                        .frame(width: 1, height: axisY + 6)
                        .offset(y: 3)
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(Color.primary.opacity(0.42))
                        .frame(width: 7, height: 3)
                }
                .offset(x: playheadX - 0.5, y: 0)
            }
        }
    }

    private func points(in size: CGSize) -> [CGPoint] {
        let display = displayValues()
        guard display.count > 1 else { return [] }
        let minValue = display.min() ?? 0
        let maxValue = display.max() ?? 1
        let span = max(maxValue - minValue, 0.01)
        return display.enumerated().map { index, value in
            let x = size.width * CGFloat(index) / CGFloat(display.count - 1)
            let normalized = CGFloat((value - minValue) / span)
            let plotTop: CGFloat = 2
            let plotHeight = max(size.height - 9, 12)
            let y = plotTop + (1 - normalized) * plotHeight
            return CGPoint(x: x, y: y)
        }
    }

    private var timeTicks: [(position: CGFloat, label: String)] {
        [
            (0.25, "6"),
            (0.50, "12"),
            (0.75, "18")
        ]
    }

    private func displayValues() -> [Double] {
        values
    }

    private func currentDayProgress() -> CGFloat {
        let calendar = Calendar.current
        let date = Date()
        let hour = calendar.component(.hour, from: date)
        let minute = calendar.component(.minute, from: date)
        return min(max(CGFloat(hour * 60 + minute) / CGFloat(24 * 60), 0), 1)
    }
}

private struct HealthDetailView: View {
    var signal: MockHealthSignal
    var curve: [Double]
    var onSleepWindowUpdate: (Date, Date) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var isEditingSleep: Bool
    @State private var manualSleepStart: Date
    @State private var manualSleepEnd: Date

    init(signal: MockHealthSignal, curve: [Double], onSleepWindowUpdate: @escaping (Date, Date) -> Void) {
        self.signal = signal
        self.curve = curve
        self.onSleepWindowUpdate = onSleepWindowUpdate
        let defaults = Self.defaultSleepDates(from: signal.sleepWindow)
        _isEditingSleep = State(initialValue: false)
        _manualSleepStart = State(initialValue: defaults.start)
        _manualSleepEnd = State(initialValue: defaults.end)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Energy Inputs")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .semibold))
                        .frame(width: 28, height: 26)
                }
                .buttonStyle(.borderless)
                .keyboardShortcut(.cancelAction)
                .help("Close")
            }
            if signal.energySource == .none {
                VStack(alignment: .leading, spacing: 5) {
                    Label("Add sleep input to build your energy curve", systemImage: "bed.double")
                        .font(.system(size: 14, weight: .semibold))
                    Text("No health signal is available yet, so Dayflow is scheduling with neutral energy.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .controlBackgroundColor).opacity(0.45))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            } else {
                EnergyCurveView(values: curve, accent: Color(nsColor: .systemGreen))
                    .frame(height: 84)
            }
            VStack(spacing: 0) {
                detailRow("Sleep", signal.sleepWindow)
                rowDivider
                detailRow("Resting HR", signal.restingHeartRate.map { "\($0) bpm" } ?? "No data")
                rowDivider
                detailRow("HRV", signal.hrv.map { "\($0) ms" } ?? "No data")
                rowDivider
                detailRow("Steps", signal.steps.map(String.init) ?? "No data")
                rowDivider
                Button {
                    withAnimation(.easeOut(duration: 0.16)) {
                        isEditingSleep.toggle()
                    }
                } label: {
                    HStack {
                        Text("Manual Sleep Input")
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(isEditingSleep ? "Editing" : "Edit")
                            .fontWeight(.medium)
                        Image(systemName: isEditingSleep ? "chevron.down" : "chevron.right")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .padding(.vertical, 8)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if isEditingSleep {
                    VStack(spacing: 8) {
                        DatePicker("Sleep Start", selection: $manualSleepStart, displayedComponents: .hourAndMinute)
                        DatePicker("Sleep End", selection: $manualSleepEnd, displayedComponents: .hourAndMinute)
                        HStack {
                            Spacer()
                            Button("Apply") {
                                onSleepWindowUpdate(manualSleepStart, manualSleepEnd)
                                withAnimation(.easeOut(duration: 0.16)) {
                                    isEditingSleep = false
                                }
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                    .font(.callout)
                    .padding(.top, 2)
                    .padding(.bottom, 8)
                }
            }
            .padding(.horizontal, 2)
            .padding(.vertical, 6)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.45))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            Text("Sleep timing can be adjusted manually when the curve needs correction.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(20)
        .frame(width: 360)
    }

    private var rowDivider: some View {
        Divider()
            .opacity(0.42)
            .padding(.leading, 0)
    }

    private func detailRow(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
        .font(.callout)
        .padding(.vertical, 8)
    }

    private static func defaultSleepDates(from label: String) -> (start: Date, end: Date) {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let fallbackStart = calendar.date(byAdding: .hour, value: 23, to: today) ?? Date()
        let fallbackEnd = calendar.date(byAdding: .hour, value: 7, to: today.addingTimeInterval(24 * 60 * 60)) ?? Date()
        let parts = label.split(separator: "-")
        guard parts.count == 2,
              let start = date(on: today, from: String(parts[0])),
              let endCandidate = date(on: today, from: String(parts[1]))
        else {
            return (fallbackStart, fallbackEnd)
        }
        let end = endCandidate <= start
            ? calendar.date(byAdding: .day, value: 1, to: endCandidate) ?? endCandidate
            : endCandidate
        return (start, end)
    }

    private static func date(on dayStart: Date, from label: String) -> Date? {
        let pieces = label.split(separator: ":")
        guard pieces.count == 2,
              let hour = Int(pieces[0]),
              let minute = Int(pieces[1])
        else {
            return nil
        }
        return Calendar.current.date(bySettingHour: hour, minute: minute, second: 0, of: dayStart)
    }
}
