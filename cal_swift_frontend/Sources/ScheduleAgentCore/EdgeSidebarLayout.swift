import CoreGraphics

public enum EdgeSidebarState: Sendable {
    case collapsed
    case expanded
}

public struct EdgeSidebarLayout: Sendable {
    public var expandedWidth: CGFloat
    public var hotEdgeWidth: CGFloat
    public var expandedTrailingInset: CGFloat
    public var expandedVerticalInset: CGFloat

    public init(
        expandedWidth: CGFloat = 390,
        hotEdgeWidth: CGFloat = 24,
        expandedTrailingInset: CGFloat = 8,
        expandedVerticalInset: CGFloat = 10
    ) {
        self.expandedWidth = expandedWidth
        self.hotEdgeWidth = hotEdgeWidth
        self.expandedTrailingInset = expandedTrailingInset
        self.expandedVerticalInset = expandedVerticalInset
    }

    public func frame(in visibleFrame: CGRect, state: EdgeSidebarState) -> CGRect {
        let width = state == .expanded ? expandedWidth : hotEdgeWidth
        let trailingInset = state == .expanded ? expandedTrailingInset : 0
        let verticalInset = state == .expanded ? expandedVerticalInset : 0
        return CGRect(
            x: visibleFrame.maxX - width - trailingInset,
            y: visibleFrame.minY + verticalInset,
            width: width,
            height: visibleFrame.height - (verticalInset * 2)
        )
    }
}
