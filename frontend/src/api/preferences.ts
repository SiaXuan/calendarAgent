import { apiFetch } from './client'
import type { UserPreferences } from './types'

export function fetchPreferences(): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/preferences')
}

export function patchPreferences(data: Partial<UserPreferences>): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/preferences', {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}
