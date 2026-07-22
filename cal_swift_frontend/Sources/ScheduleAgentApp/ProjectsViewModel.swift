import Foundation

/// Drives the Projects UI (list → detail → import → replan). Kept separate from
/// the value-type MockAssistantPanelState as its own ObservableObject, per the
/// architecture note, so project state and its network/EventKit side effects
/// live in one place.
///
/// The two headline flows:
///   • import — parse a document/text into project Tasks (dry-run preview → confirm)
///   • replan — turn the plan into a reminder change-set and apply it to the
///     local Reminders app via the verified EventKit executor.
@MainActor
final class ProjectsViewModel: ObservableObject {
    @Published var projects: [DayflowProject] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    // Detail state for the selected project.
    @Published var plan: DayflowProjectPlan?
    @Published var progress: DayflowProjectProgress?

    // Import flow.
    @Published var importPreview: DayflowImportResult?   // dry-run result awaiting confirm
    @Published var importText: String = ""
    @Published var isImporting = false
    @Published var statusMessage: String?

    private let client: DayflowAPIClient
    private let adapter: AppleCalendarAdapter

    init(client: DayflowAPIClient = DayflowAPIClient(),
         adapter: AppleCalendarAdapter = AppleCalendarAdapter()) {
        self.client = client
        self.adapter = adapter
    }

    // MARK: List / create

    func loadProjects() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            projects = try await client.listProjects()
        } catch {
            errorMessage = friendly(error)
        }
    }

    func createProject(name: String) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            let created = try await client.createProject(name: trimmed)
            projects.insert(created, at: 0)
            statusMessage = "已创建项目「\(created.name)」"
        } catch {
            errorMessage = friendly(error)
        }
    }

    func deleteProject(_ project: DayflowProject) async {
        do {
            _ = try await client.deleteProject(id: project.id)
            projects.removeAll { $0.id == project.id }
        } catch {
            errorMessage = friendly(error)
        }
    }

    // MARK: Detail

    func loadDetail(_ project: DayflowProject) async {
        errorMessage = nil
        async let planResult = try? client.fetchProjectPlan(id: project.id)
        async let progressResult = try? client.fetchProjectProgress(id: project.id)
        plan = await planResult
        progress = await progressResult
    }

    // MARK: Import (dry-run preview → confirm)

    func previewImport(project: DayflowProject, fileURL: URL? = nil) async {
        isImporting = true
        statusMessage = nil
        errorMessage = nil
        defer { isImporting = false }
        do {
            let result: DayflowImportResult
            if let fileURL {
                result = try await client.importPlan(id: project.id, fileURL: fileURL, dryRun: true)
            } else {
                result = try await client.importPlan(id: project.id, text: importText, dryRun: true)
            }
            importPreview = result
        } catch let e as DayflowRequestError {
            // 422 → not a plan / unparseable: show the server's reason.
            errorMessage = e.message ?? "无法从这份内容里读出计划。"
        } catch {
            errorMessage = friendly(error)
        }
    }

    func confirmImport(project: DayflowProject, fileURL: URL? = nil) async {
        isImporting = true
        defer { isImporting = false }
        do {
            let result: DayflowImportResult
            if let fileURL {
                result = try await client.importPlan(id: project.id, fileURL: fileURL, dryRun: false)
            } else {
                result = try await client.importPlan(id: project.id, text: importText, dryRun: false)
            }
            importPreview = nil
            importText = ""
            statusMessage = "已导入 \(result.tasks?.count ?? 0) 个任务"
            await loadDetail(project)
        } catch let e as DayflowRequestError {
            errorMessage = e.message ?? "导入失败。"
        } catch {
            errorMessage = friendly(error)
        }
    }

    func cancelImportPreview() {
        importPreview = nil
    }

    // MARK: Replan → write reminders via EventKit

    func replan(project: DayflowProject) async {
        statusMessage = nil
        errorMessage = nil
        let (granted, accessError) = await adapter.requestFullAccess()
        guard granted else {
            errorMessage = "需要「提醒事项」权限：\(accessError?.localizedDescription ?? "被拒绝")"
            return
        }
        do {
            let current = await adapter.currentAgentReminders(forProject: project.id)
            let result = try await client.replanProject(id: project.id, currentReminders: current)
            try await adapter.applyReminderChangeset(result.reminders)
            let r = result.reminders
            statusMessage = "提醒已更新：新增 \(r.create.count) · 改 \(r.update.count) · 删 \(r.delete.count) · 不变 \(r.unchanged)"
            await loadDetail(project)
        } catch {
            errorMessage = friendly(error)
        }
    }

    // MARK: Helpers

    private func friendly(_ error: Error) -> String {
        if let e = error as? DayflowRequestError { return e.message ?? "请求失败（\(e.status)）" }
        return error.localizedDescription
    }
}
