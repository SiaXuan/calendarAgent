import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// One project. Flow, top to bottom: import/decompose → review the plan snapshot
/// (计划节点) → confirm-write into the Reminders app via EventKit, grouped into a
/// list named after the project and tinted with its chosen color.
struct ProjectDetailView: View {
    let project: DayflowProject
    @ObservedObject var model: ProjectsViewModel

    @State private var isPickingFile = false
    @State private var listColor: Color = .blue

    /// The project as the model currently holds it (picks up a just-saved color).
    private var liveProject: DayflowProject {
        model.projects.first { $0.id == project.id } ?? project
    }

    private var canPreview: Bool {
        model.importFileURL != nil
            || model.importImageData != nil
            || !model.importText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var hasPlan: Bool { !(model.plan?.items.isEmpty ?? true) }

    var body: some View {
        HSplitView {
            chatPane.frame(minWidth: 280, idealWidth: 340)
            planPane.frame(minWidth: 300)
        }
        .navigationTitle(project.name)
        .task {
            await model.loadDetail(project)
            if let hex = liveProject.color { listColor = Self.color(fromHex: hex) }
        }
        .fileImporter(
            isPresented: $isPickingFile,
            allowedContentTypes: [.plainText, .pdf, UTType(filenameExtension: "docx") ?? .data]
        ) { result in
            if case let .success(url) = result { model.selectImportFile(url) }
        }
    }

    // MARK: Left — conversation

    private var chatPane: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if model.chatMessages.isEmpty {
                            Text("把课程大纲 / PRD 粘进来或附上截图，我来整理成计划；之后直接说怎么改。")
                                .font(.callout).foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.top, 8)
                        }
                        ForEach(Array(model.chatMessages.enumerated()), id: \.offset) { i, m in
                            chatBubble(m).id(i)
                        }
                        if model.isChatting {
                            HStack(spacing: 6) { ProgressView().controlSize(.small); Text("思考中…").font(.caption).foregroundStyle(.secondary) }
                                .id("typing")
                        }
                    }
                    .padding(12)
                }
                .onChange(of: model.chatMessages.count) { _, _ in
                    withAnimation { proxy.scrollTo(model.chatMessages.count - 1, anchor: .bottom) }
                }
            }
            Divider()
            composer.padding(10)
        }
    }

    private func chatBubble(_ m: DayflowChatMessage) -> some View {
        let isUser = m.role == "user"
        return HStack {
            if isUser { Spacer(minLength: 24) }
            Text(m.content)
                .font(.system(size: 13))
                .textSelection(.enabled)
                .padding(.horizontal, 10).padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: 10)
                    .fill(isUser ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.1)))
                .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
            if !isUser { Spacer(minLength: 24) }
        }
    }

    // MARK: Right — plan

    private var planPane: some View {
        Form {
            progressSection
            planSection
            multidaySection
            writeSection
        }
        .formStyle(.grouped)
    }

    // MARK: Progress

    @ViewBuilder private var progressSection: some View {
        if let progress = model.progress, progress.total > 0 {
            Section("进度") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(progress.done) / \(progress.total) 完成")
                        .font(.system(size: 13, weight: .semibold))
                    ProgressView(value: Double(progress.done), total: Double(progress.total))
                }
                .padding(.vertical, 2)
            }
        }
    }

    // MARK: Plan

    @ViewBuilder private var planSection: some View {
        if let items = model.plan?.items, !items.isEmpty {
            Section("计划节点") {
                ForEach(items) { item in
                    HStack(spacing: 8) {
                        Image(systemName: item.done ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(item.done ? .green : .secondary)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(item.title).font(.system(size: 13))
                            if let date = item.suggestedDate {
                                Text(date).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        } else {
            Section("计划节点") {
                Text("还没有节点，在左边发一句或粘贴大纲。")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Composer (chat input)

    /// The conversation input: attachment chip(s) above one text area, with
    /// attach + paste-image + send below.
    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let url = model.importFileURL {
                HStack(spacing: 6) {
                    Image(systemName: "doc.text").foregroundStyle(.blue)
                    Text(url.lastPathComponent).font(.system(size: 12)).lineLimit(1)
                    Spacer()
                    Button {
                        model.importFileURL = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(.blue.opacity(0.08)))
            }

            if let data = model.importImageData, let nsImage = NSImage(data: data) {
                HStack(spacing: 8) {
                    Image(nsImage: nsImage)
                        .resizable().scaledToFill()
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                    Text("图片").font(.system(size: 12))
                    Spacer()
                    Button {
                        model.importImageData = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(.purple.opacity(0.08)))
            }

            ZStack(alignment: .topLeading) {
                if model.importText.isEmpty {
                    Text(composerPlaceholder)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5).padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
                PastingTextEditor(text: $model.importText) { png in
                    model.importImageData = png
                    model.importFileURL = nil
                }
                .frame(minHeight: 96)
            }
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.secondary.opacity(0.3)))

            HStack(spacing: 10) {
                Button {
                    isPickingFile = true
                } label: {
                    Image(systemName: "paperclip").font(.system(size: 14, weight: .medium))
                }
                .buttonStyle(.borderless)
                .help("附加文件（.txt / .md / .pdf / .docx）")

                Button {
                    if !model.attachClipboardImage() {
                        model.statusMessage = "剪贴板里没有图片"
                    }
                } label: {
                    Image(systemName: "photo.on.rectangle").font(.system(size: 14, weight: .medium))
                }
                .buttonStyle(.borderless)
                .help("粘贴图片 / 截图（或 ⌘V）")

                Spacer()
                if model.isImporting { ProgressView().controlSize(.small) }
                Button {
                    Task { await model.submitComposer(project: project) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 20))
                }
                .buttonStyle(.plain)
                .disabled(!canPreview || model.isImporting || model.isChatting)
            }
        }
        .padding(.vertical, 2)
    }

    private var composerPlaceholder: String {
        if model.importImageData != nil || model.importFileURL != nil {
            return "说明（可选）"
        }
        return "粘贴大纲 / PRD / 目标；或附文件、⌘V 贴截图"
    }

    // MARK: Write to calendar

    private var writeSection: some View {
        Section("写入提醒") {
            HStack(spacing: 8) {
                ColorPicker(selection: $listColor, supportsOpacity: false) {
                    Text("列表颜色").font(.callout)
                }
                .onChange(of: listColor) { _, newValue in
                    Task { await model.setColor(Self.hexString(from: newValue), for: project) }
                }
                Spacer()
                Button {
                    Task { await model.writeToCalendar(project: liveProject) }
                } label: {
                    Label("写入", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!hasPlan)
            }
        }
    }

    // MARK: Multi-day distribution

    @ViewBuilder private var multidaySection: some View {
        Section("多天排程") {
            VStack(alignment: .leading, spacing: 10) {
                Button {
                    Task { await model.planMultiday(project: project) }
                } label: {
                    if model.isPlanning {
                        HStack(spacing: 6) { ProgressView().controlSize(.small); Text("排程中…") }
                    } else {
                        Label("排入多天日程", systemImage: "calendar.badge.clock")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!hasPlan || model.isPlanning)

                if let byDate = model.multiday?.byDate, !byDate.isEmpty {
                    ForEach(byDate.keys.sorted(), id: \.self) { day in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(day).font(.caption).foregroundStyle(.secondary)
                            ForEach(Array((byDate[day] ?? []).enumerated()), id: \.offset) { _, c in
                                HStack(spacing: 6) {
                                    Image(systemName: "circle.fill")
                                        .font(.system(size: 5)).foregroundStyle(.secondary)
                                    VStack(alignment: .leading, spacing: 0) {
                                        Text(c.title).font(.system(size: 12)).lineLimit(1)
                                        Text(c.taskTitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                    Spacer(minLength: 4)
                                    Text("\(c.minutes)m").font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.vertical, 1)
                    }
                }
            }
        }
    }

    // MARK: Color helpers

    private static func hexString(from color: Color) -> String {
        let ns = NSColor(color).usingColorSpace(.sRGB) ?? .white
        let r = Int((ns.redComponent * 255).rounded())
        let g = Int((ns.greenComponent * 255).rounded())
        let b = Int((ns.blueComponent * 255).rounded())
        return String(format: "#%02X%02X%02X", r, g, b)
    }

    private static func color(fromHex hex: String) -> Color {
        var s = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        s = s.trimmingCharacters(in: .whitespaces)
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return .blue }
        return Color(
            red: Double((v >> 16) & 0xFF) / 255,
            green: Double((v >> 8) & 0xFF) / 255,
            blue: Double(v & 0xFF) / 255)
    }
}

/// A plain-text editor that ALSO handles ⌘V of an image: on paste, if the
/// clipboard holds an image it's captured via `onPasteImage` (SwiftUI's
/// TextEditor swallows ⌘V, so we drop to an NSTextView to intercept it);
/// otherwise it pastes text as usual.
struct PastingTextEditor: NSViewRepresentable {
    @Binding var text: String
    var onPasteImage: (Data) -> Void

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true

        let size = scroll.contentSize
        let textView = ImagePastingTextView(frame: NSRect(origin: .zero, size: size))
        textView.onPasteImage = onPasteImage
        textView.delegate = context.coordinator
        textView.isEditable = true
        textView.isSelectable = true
        textView.isRichText = false
        textView.allowsUndo = true
        textView.font = .systemFont(ofSize: 12)
        textView.drawsBackground = false
        textView.textContainerInset = NSSize(width: 2, height: 6)
        textView.minSize = NSSize(width: 0, height: 0)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                                  height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.containerSize = NSSize(width: size.width,
                                                       height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = true
        textView.string = text

        scroll.documentView = textView
        return scroll
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        guard let textView = nsView.documentView as? ImagePastingTextView else { return }
        textView.onPasteImage = onPasteImage
        if textView.string != text { textView.string = text }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, NSTextViewDelegate {
        let parent: PastingTextEditor
        init(_ parent: PastingTextEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
        }
    }
}

final class ImagePastingTextView: NSTextView {
    var onPasteImage: ((Data) -> Void)?

    // ⌘V (a command-key event) is dispatched through performKeyEquivalent before
    // the text system's normal key handling — the reliable spot to catch an image
    // paste while the field is focused. Falls through to normal text paste when
    // the clipboard has no image.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
           event.charactersIgnoringModifiers?.lowercased() == "v",
           let png = ProjectsViewModel.clipboardImagePNG() {
            onPasteImage?(png)
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    override func paste(_ sender: Any?) {
        if let png = ProjectsViewModel.clipboardImagePNG() {
            onPasteImage?(png)
            return
        }
        super.paste(sender)
    }
}
