import Testing
@testable import ScheduleAgentCore

@Suite("Sidebar text input focus policy")
struct SidebarTextInputFocusPolicyTests {
    @Test("prepares the window for text input only while sidebar is expanded")
    func preparesWindowOnlyWhenExpanded() {
        let policy = SidebarTextInputFocusPolicy()

        #expect(policy.shouldPrepareWindowForTextInput(sidebarState: .expanded))
        #expect(!policy.shouldPrepareWindowForTextInput(sidebarState: .collapsed))
    }
}
