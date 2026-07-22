import AppKit
import SwiftUI

/// Opens Projects in a normal, titled, resizable window — NOT a sheet over the
/// edge hover-panel (that clipped and looked off). One reused window instance.
@MainActor
final class ProjectsWindowController {
    static let shared = ProjectsWindowController()
    private var window: NSWindow?
    private init() {}

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let hosting = NSHostingController(rootView: ProjectsView())
        let window = NSWindow(contentViewController: hosting)
        window.title = "项目"
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
