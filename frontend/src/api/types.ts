export interface ScheduleBlock {
  start: string
  end: string
  block_type: 'fixed' | 'meal' | 'suggested' | 'scheduled' | 'instant'
  task_id: string | null
  title: string
  cognitive_load: 'light' | 'medium' | 'deep' | null
  notes: string | null
  // Phase & Pomodoro
  phase_label: string | null
  focus_minutes: number
  break_minutes: number
  pomodoro_count: number
  // Urgency — inherited from parent task's deadline
  deadline: string | null
  // Planning chat
  is_uncertain: boolean
  // Instant reminders: false = date-only (no specific time), true = has explicit time
  has_explicit_time: boolean
}

export interface UnscheduledTask {
  parent_id: string
  title: string
  cognitive_load: 'deep' | 'medium' | 'light' | null
  estimated_minutes: number
  phase_label: string | null
  deadline: string | null
}

export interface DaySchedule {
  date: string
  energy_curve: number[]
  blocks: ScheduleBlock[]
  unscheduled: UnscheduledTask[]
  health_summary: string
}

export interface HealthInput {
  date: string
  sleep_start: string
  sleep_end: string
  resting_heart_rate?: number
  hrv?: number
  steps?: number
  active_minutes?: number
}

export interface HealthSnapshot {
  date: string
  sleep: {
    duration_hours: number
    sleep_start: string  // ISO datetime, e.g. "2026-04-12T03:00:00"
    sleep_end: string
  }
  resting_heart_rate?: number
  hrv?: number
  steps?: number
  active_minutes?: number
}

export interface UserPreferences {
  language: 'en' | 'zh-CN' | 'zh-TW' | 'ja'
  work_start: number
  work_end: number
  max_deep_work_minutes: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface TaskChatResult {
  reply: string
  decomposed_subtasks: Array<{
    parent_id: string
    title: string
    estimated_minutes: number
    cognitive_load: 'light' | 'medium' | 'deep'
    suggested_date: string | null
    phase_label: string | null
    is_instant: boolean
  }> | null
}
