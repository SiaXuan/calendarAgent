import Testing
import CoreGraphics
@testable import ScheduleAgentCore

@Suite("Edge sidebar hover policy")
struct EdgeSidebarHoverPolicyTests {
    @Test("ignores transient hover exit while cursor remains inside expanded sidebar")
    func ignoresTransientExitInsideExpandedFrame() {
        let expandedFrame = CGRect(x: 1050, y: 0, width: 390, height: 900)
        let cursor = CGPoint(x: 1435, y: 450)

        let decision = EdgeSidebarHoverPolicy().nextState(
            current: .expanded,
            hoverEvent: false,
            cursor: cursor,
            expandedFrame: expandedFrame
        )

        #expect(decision == .expanded)
    }

    @Test("collapses after hover exit when cursor leaves expanded sidebar")
    func collapsesWhenCursorLeavesExpandedFrame() {
        let expandedFrame = CGRect(x: 1050, y: 0, width: 390, height: 900)
        let cursor = CGPoint(x: 900, y: 450)

        let decision = EdgeSidebarHoverPolicy().nextState(
            current: .expanded,
            hoverEvent: false,
            cursor: cursor,
            expandedFrame: expandedFrame
        )

        #expect(decision == .collapsed)
    }
}
