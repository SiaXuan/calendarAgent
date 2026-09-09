import { apiFetch } from './client'

export interface Task {
  id: string
  title: string
  description: string | null
  priority: 'high' | 'medium' | 'low'
  cognitive_load: 'light' | 'medium' | 'deep'
  estimated_hours: number
  deadline: string | null
  source: string
}

export function fetchTasks(): Promise<Task[]> {
  return apiFetch<Task[]>('/tasks')
}

export function syncReminders() {
  return apiFetch('/tasks/sync/reminders', { method: 'POST' })
}

export function reclassifyTasks(): Promise<{ reclassified: number; results: Array<{ id: string; title: string; cognitive_load: string }> }> {
  return apiFetch('/tasks/reclassify', { method: 'POST' })
}
