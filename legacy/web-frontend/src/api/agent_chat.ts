import { apiFetch } from './client'
import type { DaySchedule } from './types'

export type TerminalState =
  | 'success'        // committed a minor change
  | 'proposal'       // major change staged, awaiting confirm
  | 'clarification'  // agent needs info (message is its question)
  | 'degraded'       // couldn't do it / error (schedule unchanged)
  | 'no_change'      // nothing needed

export interface ProposalChange {
  op: 'move' | 'remove' | 'add'
  scratch_id: string
  title: string
  block_type: string
  cross_day?: boolean
  touches_synced?: boolean
  from_time?: string | null   // move→old time; remove→where it was
  to_time?: string | null     // move→new time; add→where it goes
}

export interface AgentProposal {
  proposal_id: string
  summary: string
  preview: DaySchedule
  changes: ProposalChange[]
}

export interface AgentChatResult {
  terminal_state: TerminalState
  message: string
  schedule: DaySchedule | null
  proposal: AgentProposal | null
}

/** Send a message to the conversational schedule agent. Pass a signal to allow interruption. */
export function sendAgentMessage(
  date: string, message: string, signal?: AbortSignal,
): Promise<AgentChatResult> {
  return apiFetch<AgentChatResult>('/chat/agent', {
    method: 'POST',
    body: JSON.stringify({ date, message }),
    signal,
  })
}

/** Apply a pending major Proposal (server checks version + TTL). */
export function confirmProposal(date: string): Promise<AgentChatResult> {
  return apiFetch<AgentChatResult>('/chat/agent/confirm', {
    method: 'POST',
    body: JSON.stringify({ date }),
  })
}
