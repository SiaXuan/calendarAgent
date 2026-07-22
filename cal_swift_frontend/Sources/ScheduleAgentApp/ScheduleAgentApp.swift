import SwiftUI
import ScheduleAgentCore

/// Process entry point. `--verify-eventkit` runs a headless round-trip against
/// the real Calendar/Reminders (see EventKitVerification) instead of launching
/// the GUI, so the EventKit executor can be exercised on-device manually.
@main
enum AppEntry {
    static func main() {
        if CommandLine.arguments.contains("--verify-eventkit") {
            EventKitVerification.run()
            return
        }
        ScheduleAgentApp.main()
    }
}

struct ScheduleAgentApp: App {
    var body: some Scene {
        WindowGroup {
            HoverSidebarRoot()
                .background(WindowConfigurator())
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

struct HoverSidebarRoot: View {
    @State private var isExpanded = false
    @State private var isPinned = false

    var body: some View {
        ZStack(alignment: .trailing) {
            SidebarView(isPinned: isPinned) {
                isPinned.toggle()
                SidebarWindowController.shared.setPinned(isPinned)
                if isPinned {
                    isExpanded = true
                }
            }
            .frame(width: isExpanded ? EdgeSidebarLayout().expandedWidth : 0)
            .clipped()
            .opacity(isExpanded ? 1 : 0)
            .allowsHitTesting(isExpanded)

            if !isExpanded {
                EdgeHotZone()
                    .frame(width: EdgeSidebarLayout().hotEdgeWidth)
                    .frame(maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onHover { hovering in
            guard !isPinned else { return }
            let expanded = SidebarWindowController.shared.handleHover(hovering)
            withAnimation(.easeOut(duration: 0.16)) {
                isExpanded = expanded
            }
        }
    }
}

private struct EdgeHotZone: View {
    var body: some View {
        Color.clear
            .contentShape(Rectangle())
            .overlay(alignment: .center) {
                Capsule()
                    .fill(Color.primary.opacity(0.82))
                    .frame(width: 5, height: 84)
                    .shadow(color: .black.opacity(0.18), radius: 5, x: 0, y: 1)
            }
        .help("Move here to open Schedule Agent")
    }
}

private struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            guard let window = view.window else { return }
            NSApp.setActivationPolicy(.regular)
            window.level = .floating
            window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            window.title = "Schedule Agent"
            window.isMovableByWindowBackground = false
            window.backgroundColor = .clear
            window.isOpaque = false
            window.hasShadow = false
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.standardWindowButton(.closeButton)?.isHidden = true
            window.standardWindowButton(.miniaturizeButton)?.isHidden = true
            window.standardWindowButton(.zoomButton)?.isHidden = true
            SidebarWindowController.shared.attach(window)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

@MainActor
final class SidebarWindowController {
    static let shared = SidebarWindowController()

    private let layout = EdgeSidebarLayout(expandedWidth: 390)
    private let hoverPolicy = EdgeSidebarHoverPolicy()
    private let textInputFocusPolicy = SidebarTextInputFocusPolicy()
    private weak var window: NSWindow?
    private var isExpanded = false
    private var isPinned = false

    private init() {}

    func attach(_ window: NSWindow) {
        self.window = window
        window.acceptsMouseMovedEvents = true
        window.ignoresMouseEvents = false
        setExpanded(false, animated: false)
    }

    func setPinned(_ pinned: Bool) {
        isPinned = pinned
        if pinned {
            setExpanded(true)
        }
    }

    func handleHover(_ hovering: Bool) -> Bool {
        if isPinned {
            setExpanded(true)
            return true
        }

        guard let window else {
            return hovering
        }

        let expandedFrame = frame(for: .expanded, window: window)
        let next = hoverPolicy.nextState(
            current: isExpanded ? .expanded : .collapsed,
            hoverEvent: hovering,
            cursor: NSEvent.mouseLocation,
            expandedFrame: expandedFrame
        )
        setExpanded(next == .expanded)
        return next == .expanded
    }

    func setExpanded(_ expanded: Bool, animated: Bool = true) {
        guard expanded != isExpanded || window != nil else { return }
        isExpanded = expanded
        guard let window else { return }
        let target = frame(for: expanded ? .expanded : .collapsed, window: window)
        if animated {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.18
                context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                window.animator().setFrame(target, display: true)
            }
        } else {
            window.setFrame(target, display: true)
        }
    }

    func prepareForTextInput() {
        guard textInputFocusPolicy.shouldPrepareWindowForTextInput(sidebarState: isExpanded ? .expanded : .collapsed),
              let window
        else { return }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    private func frame(for state: EdgeSidebarState, window: NSWindow) -> CGRect {
        guard let screen = window.screen ?? NSScreen.main else {
            return CGRect(x: 0, y: 0, width: layout.hotEdgeWidth, height: 720)
        }
        return layout.frame(in: screen.visibleFrame, state: state)
    }
}
