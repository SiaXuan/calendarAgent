import { apiFetch } from './client'

export type MemoryNamespace =
  | 'schedule_prefs'
  | 'task_lexicon'
  | 'physiological'
  | 'interactions'

export interface Memory {
  id: string
  namespace: [string, MemoryNamespace]
  content: string
  structured: Record<string, unknown> | null
  confidence: number
  source_event_ids: string[]
  created_at: string   // ISO datetime
  last_reinforced_at: string
  decay_rate: number
  user_verified: boolean
}

export interface MemoryListOpts {
  namespace?: MemoryNamespace
  min_confidence?: number
  include_unverified?: boolean
}

export function listMemories(opts: MemoryListOpts = {}): Promise<Memory[]> {
  const q = new URLSearchParams()
  if (opts.namespace) q.set('namespace', opts.namespace)
  if (opts.min_confidence !== undefined) q.set('min_confidence', String(opts.min_confidence))
  if (opts.include_unverified !== undefined) q.set('include_unverified', String(opts.include_unverified))
  const suffix = q.toString() ? `?${q}` : ''
  return apiFetch<Memory[]>(`/memory${suffix}`)
}

export interface MemoryPatch {
  content?: string
  confidence?: number
  user_verified?: boolean
}

export function patchMemory(id: string, patch: MemoryPatch): Promise<Memory> {
  return apiFetch<Memory>(`/memory/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteMemory(id: string): Promise<{ deleted: string }> {
  return apiFetch(`/memory/${id}`, { method: 'DELETE' })
}

export interface MemoryCreate {
  namespace: MemoryNamespace
  content: string
  confidence?: number
  structured?: Record<string, unknown> | null
  user_verified?: boolean
}

export function createMemory(payload: MemoryCreate): Promise<Memory> {
  return apiFetch<Memory>('/memory', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ── Feedback (Phase C.3) ─────────────────────────────────────────────────────

export interface FeedbackPayload {
  action: 'accept' | 'dismiss'
  block_key: string
  hour: number
  task_kind?: string | null
  cognitive_load?: string | null
}

export interface FeedbackResponse {
  observation: { id: string; action: string; hour_bucket: string }
  promoted: Memory[]
}

/**
 * Fire-and-forget — the schedule timeline records every accept/dismiss
 * so the backend can learn user patterns. Returns promoted memories if any
 * N-threshold was crossed by this signal.
 */
export function submitFeedback(payload: FeedbackPayload): Promise<FeedbackResponse> {
  return apiFetch<FeedbackResponse>('/memory/feedback', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
