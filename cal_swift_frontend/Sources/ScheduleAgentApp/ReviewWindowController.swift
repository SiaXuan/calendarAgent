import AppKit
import SwiftUI

/// Opens the completion review (heatmap "commit wall" + recent completions) in a
/// normal titled window — same pattern as ProjectsWindowController (a sheet over
/// the clipped edge-panel looked off). One reused window instance.
@MainActor
final class ReviewWindowController {
    static let shared = ReviewWindowController()
    private var window: NSWindow?
    private init() {}

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let hosting = NSHostingController(rootView: ReviewView())
        let window = NSWindow(contentViewController: hosting)
        window.title = "复盘"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 620, height: 640))
        window.isReleasedWhenClosed = false
        window.center()
        window.level = .normal
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
