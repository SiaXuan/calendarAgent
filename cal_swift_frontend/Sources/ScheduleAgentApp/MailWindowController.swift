import AppKit
import SwiftUI

/// Opens the Mail triage window (the agent's report of today's new mail).
/// Same one-reused-window pattern as ReviewWindowController.
@MainActor
final class MailWindowController {
    static let shared = MailWindowController()
    private var window: NSWindow?
    private init() {}

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let hosting = NSHostingController(rootView: MailView())
        let window = NSWindow(contentViewController: hosting)
        window.title = "邮件"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 560, height: 640))
        window.isReleasedWhenClosed = false
        window.center()
        window.level = .normal
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
