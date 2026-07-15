import Testing
@testable import ScheduleAgentCore

@Suite("Agent chat refresh policy")
struct AgentChatRefreshPolicyTests {
    @Test("reloads schedule after successful committed agent changes")
    func reloadsScheduleAfterSuccess() {
        let policy = AgentChatRefreshPolicy()

        #expect(policy.shouldReloadSchedule(terminalState: "success"))
        #expect(!policy.shouldReloadSchedule(terminalState: "proposal"))
        #expect(!policy.shouldReloadSchedule(terminalState: "clarification"))
        #expect(!policy.shouldReloadSchedule(terminalState: "degraded"))
        #expect(!policy.shouldReloadSchedule(terminalState: "no_change"))
    }

    @Test("shows a pending proposal instead of applying it")
    func showsPendingProposal() {
        let policy = AgentChatRefreshPolicy()

        #expect(policy.shouldPresentProposal(terminalState: "proposal"))
        #expect(!policy.shouldPresentProposal(terminalState: "success"))
    }
}
