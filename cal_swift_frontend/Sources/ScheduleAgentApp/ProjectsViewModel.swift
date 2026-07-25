import AppKit
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
    @Published var multiday: DayflowMultidayPlan?      // per-day distribution
    @Published var chatMessages: [DayflowChatMessage] = []   // project conversation
    @Published var isChatting = false

    // Import flow.
    @Published var importPreview: DayflowImportResult?   // dry-run result awaiting confirm
    // One composer field. With no file it's the plan text (may embed an
    // instruction like "move to 2027"); with a file attached it's the note about
    // how to handle that file.
    @Published var importText: String = ""
    @Published var importFileURL: URL?                   // set when a file was attached
    @Published var importImageData: Data?                // set when an image was pasted/attached (PNG)
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
        async let multidayResult = try? client.fetchMultidayPlan(id: project.id)
        async let chatResult = try? client.fetchProjectChat(id: project.id)
        plan = await planResult
        progress = await progressResult
        multiday = await multidayResult
        chatMessages = (await chatResult)?.messages ?? []
    }

    // MARK: Planning conversation

    /// Send the composer as one conversation turn — text and/or an attachment
    /// (pasted image / file). Everything goes through the planning chat; there's
    /// no separate import step or intent gate. The plan (right pane) refreshes
    /// when the turn changed it.
    func submitComposer(project: DayflowProject) async {
        let text = importText.trimmingCharacters(in: .whitespacesAndNewlines)
        let image = importImageData
        let fileURL = importFileURL
        guard !text.isEmpty || image != nil || fileURL != nil else { return }

        let label = image != nil ? "［图片］" : (fileURL != nil ? "［\(fileURL!.lastPathComponent)］" : "")
        let bubble = [label, text].filter { !$0.isEmpty }.joined(separator: " ")
        chatMessages.append(DayflowChatMessage(role: "user", content: bubble))
        importText = ""; importImageData = nil; importFileURL = nil

        isChatting = true
        errorMessage = nil
        defer { isChatting = false }
        do {
            let reply = try await client.sendProjectChat(
                id: project.id, message: text, imageData: image, fileURL: fileURL)
            chatMessages.append(DayflowChatMessage(role: "assistant", content: reply.reply))
            if reply.planChanged { await loadDetail(project) }
        } catch {
            errorMessage = friendly(error)
        }
    }

    /// Distribute all active project work across days. Reads the next 30 days of
    // Multi-day distribution is now filled automatically during daily schedule
    // generation (backend `ensure_multiday_plan`); the manual "plan multi-day"
    // action was removed. `loadDetail` still fetches the distribution to show it.

    // MARK: Import (dry-run preview → confirm)

    /// Pick a file for import — remembered so the confirm step re-imports the same
    /// file (not the empty text field).
    func selectImportFile(_ url: URL) { importFileURL = url }

    /// Grab an image off the clipboard (pasted screenshot / photo) as PNG, so it
    /// can be imported via Claude vision. Returns whether an image was found.
    @discardableResult
    func attachClipboardImage() -> Bool {
        guard let png = Self.clipboardImagePNG() else { return false }
        importImageData = png
        importFileURL = nil
        return true
    }

    static func clipboardImagePNG() -> Data? {
        let pb = NSPasteboard.general

        // 1) Raw image data on the pasteboard (screenshots, copied images).
        for type in [NSPasteboard.PasteboardType.png, .tiff] where pb.data(forType: type) != nil {
            if let data = pb.data(forType: type) {
                if type == .png { return data }
                if let png = pngFromTIFF(data) { return png }
            }
        }
        // 2) NSImage from the pasteboard (covers more sources / representations).
        if let image = NSImage(pasteboard: pb), let png = pngFromImage(image) { return png }

        // 3) A copied image FILE (e.g. dragged/copied from Finder).
        if let urls = pb.readObjects(forClasses: [NSURL.self]) as? [URL] {
            let exts = ["png", "jpg", "jpeg", "gif", "webp", "heic", "tiff", "bmp"]
            for url in urls where exts.contains(url.pathExtension.lowercased()) {
                if let data = try? Data(contentsOf: url),
                   let image = NSImage(data: data), let png = pngFromImage(image) {
                    return png
                }
            }
        }
        return nil
    }

    private static func pngFromImage(_ image: NSImage) -> Data? {
        guard let tiff = image.tiffRepresentation else { return nil }
        return pngFromTIFF(tiff)
    }

    private static func pngFromTIFF(_ tiff: Data) -> Data? {
        NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
    }

    /// Dry-run preview — no persistence, no reminder access prompt.
    func previewImport(project: DayflowProject) async {
        isImporting = true
        statusMessage = nil
        errorMessage = nil
        defer { isImporting = false }
        let composer = importText.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            importPreview = try await callImport(project: project, composer: composer, dryRun: true)
        } catch let e as DayflowRequestError {
            errorMessage = e.message ?? "无法从这份内容里读出计划。"
        } catch {
            errorMessage = friendly(error)
        }
    }

    /// Confirm — create the project's tasks and write the plan snapshot (fills
    /// "计划节点" for review). Reminders are NOT written here; the user reviews the
    /// snapshot and then presses "写入日历" (→ replan) to sync the Reminders app.
    func confirmImport(project: DayflowProject) async {
        isImporting = true
        statusMessage = nil
        errorMessage = nil
        defer { isImporting = false }
        let composer = importText.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let result = try await callImport(project: project, composer: composer, dryRun: false)
            clearImportInputs()
            statusMessage = "已导入 \(result.tasks?.count ?? 0) 个任务"
            await loadDetail(project)
        } catch let e as DayflowRequestError {
            errorMessage = e.message ?? "导入失败。"
        } catch {
            errorMessage = friendly(error)
        }
    }

    private func callImport(
        project: DayflowProject, composer: String, dryRun: Bool
    ) async throws -> DayflowImportResult {
        if let image = importImageData {
            // Pasted image → Claude vision; composer text is a note about it.
            return try await client.importPlan(
                id: project.id, imageData: image, instruction: composer, dryRun: dryRun)
        }
        if let url = importFileURL {
            // File is the content; the composer text is a note about it.
            return try await client.importPlan(
                id: project.id, fileURL: url, instruction: composer, dryRun: dryRun)
        }
        // Composer text IS the plan; the backend picks up any embedded instruction.
        return try await client.importPlan(id: project.id, text: composer, dryRun: dryRun)
    }

    func cancelImportPreview() {
        importPreview = nil
        importFileURL = nil
        importImageData = nil
    }

    private func clearImportInputs() {
        importPreview = nil
        importText = ""
        importFileURL = nil
        importImageData = nil
    }

    // MARK: Write to calendar → project's reminders via EventKit

    /// Write the current plan into the Reminders app: diff against the reminders
    /// we already own for this project and apply the change-set into a list named
    /// after the project, tinted with its color.
    func writeToCalendar(project: DayflowProject) async {
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
            try await adapter.applyReminderChangeset(
                result.reminders, listName: project.name, colorHex: project.color)
            let r = result.reminders
            statusMessage = "已写入：+\(r.create.count) ·改\(r.update.count) ·删\(r.delete.count)"
            await loadDetail(project)
        } catch {
            errorMessage = friendly(error)
        }
    }

    /// Persist a project's chosen color (used to tint its reminder list). Updates
    /// the in-memory list so the detail view reflects it immediately.
    func setColor(_ hex: String, for project: DayflowProject) async {
        do {
            let updated = try await client.updateProject(id: project.id, color: hex)
            if let i = projects.firstIndex(where: { $0.id == updated.id }) {
                projects[i] = updated
            }
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
