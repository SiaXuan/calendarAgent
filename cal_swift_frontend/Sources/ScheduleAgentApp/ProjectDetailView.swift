import SwiftUI
import UniformTypeIdentifiers

/// One project: progress + planned items, plus the two headline actions —
/// import a plan (dry-run preview → confirm) and replan (write reminders to the
/// local Reminders app via EventKit).
struct ProjectDetailView: View {
    let project: DayflowProject
    @ObservedObject var model: ProjectsViewModel

    @State private var isPickingFile = false

    private var canPreview: Bool {
        model.importFileURL != nil
            || !model.importText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        Form {
            progressSection
            planSection
            importSection
            replanSection
            if let status = model.statusMessage {
                Section { Text(status).font(.callout).foregroundStyle(.secondary) }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(project.name)
        .task { await model.loadDetail(project) }
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
                Text("还没有节点。先导入一份计划，或用聊天拆解任务。")
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
                VStack(alignment: .leading, spacing: 8) {
                    if let url = model.importFileURL {
                        HStack(spacing: 6) {
                            Image(systemName: "doc").foregroundStyle(.secondary)
                            Text(url.lastPathComponent).font(.system(size: 12))
                            Spacer()
                            Button("移除") { model.importFileURL = nil }
                                .buttonStyle(.plain).foregroundStyle(.secondary)
                        }
                    } else {
                        Text("粘贴课程大纲 / PRD / 一段目标文字，或选一个文件（.txt / .md / .pdf / .docx）。")
                            .font(.caption).foregroundStyle(.secondary)
                        TextEditor(text: $model.importText)
                            .font(.system(size: 12))
                            .frame(minHeight: 80)
                            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.secondary.opacity(0.3)))
                    }

                    Text("说明（可选）")
                        .font(.caption).foregroundStyle(.secondary)
                    TextField("例：这是 25 年的大纲，挪到 27 年秋季学期，作业仍周一交", text: $model.importInstruction, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...3)
                    Text("说明会用来平移日期（改年份/学期起点、改成周几）。日期由后端精确计算。")
                        .font(.caption2).foregroundStyle(.secondary)

                    HStack {
                        Button("选择文件…") { isPickingFile = true }
                        Spacer()
                        Button("预览") {
                            Task { await model.previewImport(project: project) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!canPreview || model.isImporting)
                    }
                    if model.isImporting { ProgressView().controlSize(.small) }
                }
            }
        }
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

    // MARK: Replan

    private var replanSection: some View {
        Section("排入提醒") {
            VStack(alignment: .leading, spacing: 6) {
                Text("按当前计划重排，把未来节点写成「提醒事项」App 里的待办（已完成的保留、变了的替换）。")
                    .font(.caption).foregroundStyle(.secondary)
                Button {
                    Task { await model.replan(project: project) }
                } label: {
                    Label("重排并写入提醒", systemImage: "arrow.triangle.2.circlepath")
                }
            }
        }
    }
}
