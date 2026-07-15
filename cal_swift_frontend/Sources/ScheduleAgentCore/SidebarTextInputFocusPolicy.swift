public struct SidebarTextInputFocusPolicy: Sendable {
    public init() {}

    public func shouldPrepareWindowForTextInput(sidebarState: EdgeSidebarState) -> Bool {
        sidebarState == .expanded
    }
}
