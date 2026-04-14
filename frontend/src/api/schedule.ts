import { apiFetch } from './client'
import type { DaySchedule, ScheduleBlock, UnscheduledTask } from './types'

export function fetchSchedule(date: string): Promise<DaySchedule> {
  return apiFetch<DaySchedule>(`/schedule/${date}`)
}

export function generateSchedule(date: string): Promise<DaySchedule> {
  return apiFetch<DaySchedule>('/schedule/generate', {
    method: 'POST',
    body: JSON.stringify({ date }),
  })
}

// ── SSE streaming ─────────────────────────────────────────────────────────────

export type StreamEvent =
  | { type: 'health'; energy_curve: number[]; health_summary: string }
  | { type: 'fixed'; blocks: ScheduleBlock[] }
  | { type: 'schedule'; blocks: ScheduleBlock[]; unscheduled: UnscheduledTask[] }
  | { type: 'done' }
  | { type: 'error'; message: string }

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

/**
 * Open an SSE connection to /schedule/stream/{date}.
 * Calls onEvent for each incoming event; calls onDone when the stream closes.
 * Returns a cleanup function that closes the EventSource.
 */
export function streamSchedule(
  date: string,
  onEvent: (e: StreamEvent) => void,
  onDone?: () => void,
): () => void {
  const es = new EventSource(`${BASE}/schedule/stream/${date}`)

  es.onmessage = (raw) => {
    try {
      const evt = JSON.parse(raw.data) as StreamEvent
      onEvent(evt)
      if (evt.type === 'done' || evt.type === 'error') {
        es.close()
        onDone?.()
      }
    } catch {}
  }

  es.onerror = () => {
    es.close()
    onDone?.()
  }

  return () => es.close()
}
