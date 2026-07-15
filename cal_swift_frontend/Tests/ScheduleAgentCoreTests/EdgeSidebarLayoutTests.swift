import Testing
import Foundation
import CoreGraphics
@testable import ScheduleAgentCore

@Suite("Edge sidebar layout")
struct EdgeSidebarLayoutTests {
    @Test("collapsed sidebar leaves only a hot edge visible")
    func collapsedSidebarLeavesHotEdge() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let layout = EdgeSidebarLayout(expandedWidth: 390, hotEdgeWidth: 24)

        let frame = layout.frame(in: screen, state: .collapsed)

        #expect(frame.minX == 1416)
        #expect(frame.width == 24)
        #expect(frame.height == 900)
    }

    @Test("default collapsed sidebar uses a visible hot edge")
    func defaultCollapsedSidebarUsesVisibleHotEdge() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let frame = EdgeSidebarLayout().frame(in: screen, state: .collapsed)

        #expect(frame.width == 24)
    }

    @Test("expanded sidebar slides out from the right edge")
    func expandedSidebarSlidesOut() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let layout = EdgeSidebarLayout(expandedWidth: 390, hotEdgeWidth: 12, expandedTrailingInset: 8, expandedVerticalInset: 10)

        let frame = layout.frame(in: screen, state: .expanded)

        #expect(frame.minX == 1042)
        #expect(frame.minY == 10)
        #expect(frame.width == 390)
        #expect(frame.height == 880)
    }
}
