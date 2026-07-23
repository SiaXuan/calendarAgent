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
            || !model.importText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var hasPlan: Bool { !(model.plan?.items.isEmpty ?? true) }

    var body: some View {
        Form {
            progressSection
            planSection
            importSection
            writeSection
            if let status = model.statusMessage {
                Section { Text(status).font(.callout).foregroundStyle(.secondary) }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(project.name)
        .task {
            await model.loadDetail(project)
            if let hex = liveProject.color { listColor = Self.color(fromHex: hex) }
        }
        .fileImporter(
            isPresented: $isPickingFile,
            allowedContentTypes: [.plainText, .pdf, UTType(filenameExtension: "docx") ?? .data]
        ) { result in
            // Select only — don't preview yet, so the user can add an instruction first.
            if case let .success(url) = result { model.selectImportFile(url) }
        }
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
                Text("还没有节点。先在下面导入一份计划（课程大纲 / PRD / 一段目标）。")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Import

    private var importSection: some View {
        Section("导入计划") {
            if let preview = model.importPreview {
                importPreviewCard(preview)
            } else {
                composer
            }
        }
    }

    /// Chat-style composer: an attachment chip (when a file is picked) above one
    /// text area, with attach + preview actions below.
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

            ZStack(alignment: .topLeading) {
                if model.importText.isEmpty {
                    Text(composerPlaceholder)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5).padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $model.importText)
                    .font(.system(size: 12))
                    .frame(minHeight: 96)
                    .scrollContentBackground(.hidden)
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

                Spacer()
                if model.isImporting { ProgressView().controlSize(.small) }
                Button("预览") { Task { await model.previewImport(project: project) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canPreview || model.isImporting)
            }
        }
        .padding(.vertical, 2)
    }

    private var composerPlaceholder: String {
        model.importFileURL == nil
            ? "粘贴课程大纲 / PRD / 一段目标，或点回形针附加文件。想改年份或周几，直接在这写。"
            : "对这份文件的说明（可选），例如：挪到 2027 年秋季，作业仍周一交。"
    }

    private func importPreviewCard(_ preview: DayflowImportResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if let kind = preview.docKind { Text(kind).font(.caption).foregroundStyle(.secondary) }
                if let c = preview.confidence {
                    Text("把握 \(Int(c * 100))%").font(.caption).foregroundStyle(.secondary)
                }
            }
            Text("将导入 \(preview.tasks?.count ?? 0) 个任务：")
                .font(.system(size: 13, weight: .semibold))
            ForEach(preview.tasks ?? []) { task in
                HStack(spacing: 6) {
                    Image(systemName: "circle").font(.system(size: 8)).foregroundStyle(.secondary)
                    Text(task.title).font(.system(size: 12))
                    if let d = task.deadline { Text(d).font(.caption2).foregroundStyle(.secondary) }
                }
            }
            HStack {
                Button("取消") { model.cancelImportPreview() }
                Spacer()
                Button("确认导入") {
                    Task { await model.confirmImport(project: project) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isImporting)
            }
            .padding(.top, 4)
        }
        .padding(.vertical, 2)
    }

    // MARK: Write to calendar

    private var writeSection: some View {
        Section("写入日历") {
            VStack(alignment: .leading, spacing: 10) {
                Text("复核上面的计划节点后，写成「提醒事项」App 里的待办——按项目分到一个以项目名命名的列表，用下面选的颜色标记。改过任务或勾了完成后再点一次即可重新同步（已完成的保留、变了的替换、删掉的清除）。")
                    .font(.caption).foregroundStyle(.secondary)

                HStack(spacing: 8) {
                    ColorPicker(selection: $listColor, supportsOpacity: false) {
                        Text("列表颜色").font(.callout)
                    }
                    .onChange(of: listColor) { _, newValue in
                        Task { await model.setColor(Self.hexString(from: newValue), for: project) }
                    }
                    Spacer()
                }

                Button {
                    Task { await model.writeToCalendar(project: liveProject) }
                } label: {
                    Label("写入日历（提醒）", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!hasPlan)
                if !hasPlan {
                    Text("先导入计划生成节点，才能写入。")
                        .font(.caption2).foregroundStyle(.secondary)
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
