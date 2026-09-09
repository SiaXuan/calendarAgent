import { apiFetch } from './client'
import type { HealthInput, HealthSnapshot } from './types'

export function postHealth(data: HealthInput) {
  return apiFetch('/health', { method: 'POST', body: JSON.stringify(data) })
}

export function getHealth(date: string): Promise<HealthSnapshot> {
  return apiFetch(`/health/${date}`)
}
