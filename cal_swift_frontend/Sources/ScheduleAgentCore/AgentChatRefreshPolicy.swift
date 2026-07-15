public struct AgentChatRefreshPolicy: Sendable {
    public init() {}

    public func shouldReloadSchedule(terminalState: String) -> Bool {
        terminalState == "success"
    }

    public func shouldPresentProposal(terminalState: String) -> Bool {
        terminalState == "proposal"
    }
}
