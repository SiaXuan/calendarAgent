import { apiFetch } from './client'
import type { ChatMessage, TaskChatResult } from './types'

export function sendTaskMessage(
  taskId: string,
  messages: ChatMessage[],
  targetDate: string,
): Promise<TaskChatResult> {
  return apiFetch<TaskChatResult>(`/chat/task/${taskId}`, {
    method: 'POST',
    body: JSON.stringify({ messages, target_date: targetDate }),
  })
}

export function confirmTaskPlan(
  taskId: string,
  subtasks: TaskChatResult['decomposed_subtasks'],
): Promise<{ confirmed: number; task_id: string }> {
  return apiFetch(`/chat/task/${taskId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ subtasks }),
  })
}
