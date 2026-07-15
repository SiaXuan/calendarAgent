import Foundation

public enum CalendarWriteError: Error, Equatable, Sendable {
    case planNotConfirmed
    case blockNotAccepted
}

public protocol CalendarWriting {
    func write(plan: SchedulePlan, confirmation: PlanConfirmation?) throws
}

public final class InMemoryCalendarWriter: CalendarWriting {
    public private(set) var writtenBlocks: [ScheduledBlock] = []

    public init() {}

    public func write(plan: SchedulePlan, confirmation: PlanConfirmation?) throws {
        guard let confirmation else {
            throw CalendarWriteError.planNotConfirmed
        }

        let accepted = plan.scheduledBlocks.filter { confirmation.acceptedBlockIDs.contains($0.id) }
        guard accepted.count == confirmation.acceptedBlockIDs.count else {
            throw CalendarWriteError.blockNotAccepted
        }

        writtenBlocks.append(contentsOf: accepted)
    }
}
