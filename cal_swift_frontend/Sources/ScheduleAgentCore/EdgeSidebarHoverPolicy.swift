import CoreGraphics

public struct EdgeSidebarHoverPolicy: Sendable {
    public init() {}

    public func nextState(
        current: EdgeSidebarState,
        hoverEvent: Bool,
        cursor: CGPoint,
        expandedFrame: CGRect
    ) -> EdgeSidebarState {
        if hoverEvent {
            return .expanded
        }

        if current == .expanded, expandedFrame.contains(cursor) {
            return .expanded
        }

        return .collapsed
    }
}
