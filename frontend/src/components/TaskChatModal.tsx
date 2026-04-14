import { useState, useRef, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { sendTaskMessage, confirmTaskPlan } from '../api/chat_task'
import type { ChatMessage, ScheduleBlock, TaskChatResult } from '../api/types'

interface Props {
  block: ScheduleBlock
  onClose: () => void
  onConfirmed: () => void  // called after plan is confirmed → triggers schedule regenerate
}

export default function TaskChatModal({ block, onClose, onConfirmed }: Props) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [pendingPlan, setPendingPlan] = useState<TaskChatResult['decomposed_subtasks']>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const today = new Date().toISOString().slice(0, 10)
  const taskId = block.task_id ?? ''

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input on open
  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 100)
  }, [])

  const chatMutation = useMutation({
    mutationFn: (text: string) => {
      const newMessages: ChatMessage[] = [...messages, { role: 'user', content: text }]
      setMessages(newMessages)
      return sendTaskMessage(taskId, newMessages, today)
    },
    onSuccess: (result) => {
      setMessages(m => [...m, { role: 'assistant', content: result.reply }])
      if (result.decomposed_subtasks) {
        setPendingPlan(result.decomposed_subtasks)
      }
    },
  })

  const confirmMutation = useMutation({
    mutationFn: () => confirmTaskPlan(taskId, pendingPlan),
    onSuccess: () => {
      // Invalidate cached schedule so TodayPage refetches
      qc.invalidateQueries({ queryKey: ['schedule'] })
      onConfirmed()
      onClose()
    },
  })

  function send() {
    const text = input.trim()
    if (!text || chatMutation.isPending) return
    setInput('')
    chatMutation.mutate(text)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: 'rgba(15, 28, 44, 0.45)', backdropFilter: 'blur(2px)' }}
    >
      {/* slide-up sheet */}
      <div
        className="absolute bottom-0 left-0 right-0 bg-white rounded-t-2xl flex flex-col"
        style={{ height: '85dvh', maxHeight: '85dvh' }}
      >
        {/* drag handle */}
        <div className="flex justify-center pt-3 pb-1 flex-shrink-0">
          <div className="w-8 h-1 rounded-full bg-[#D8DDE3]" />
        </div>

        {/* header */}
        <div className="px-4 pb-3 border-b border-ice2 flex items-start gap-3 flex-shrink-0">
          <div className="flex-1">
            <div className="text-[14px] font-semibold text-[#1a2730] leading-snug">{block.title}</div>
            <div className="text-[11px] text-gray-text mt-0.5">
              {block.phase_label
                ? `${block.phase_label} · ${t('planningChat')}`
                : t('planningChat')}
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-full bg-ice flex items-center justify-center text-gray-text hover:bg-ice2"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>

        {/* messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-2.5">
          {messages.length === 0 && (
            <div className="flex flex-col gap-2 mt-4">
              <p className="text-[13px] text-gray-text text-center">
                {t('chatEmpty')}
              </p>
              {/* starter prompts */}
              {[
                t('chatStarter1'),
                t('chatStarter2'),
                t('chatStarter3'),
              ].map((prompt, i) => (
                <button
                  key={i}
                  onClick={() => { setInput(prompt); inputRef.current?.focus() }}
                  className="text-left px-3 py-2.5 rounded-xl bg-ice border border-steel text-[12px] text-blue-text"
                >
                  {prompt}
                </button>
              ))}
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              {m.role === 'assistant' && (
                <div className="w-5 h-5 rounded-full bg-blue flex items-center justify-center mr-1.5 flex-shrink-0 mt-0.5">
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="white">
                    <circle cx="12" cy="12" r="4"/>
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" stroke="white" strokeWidth="1.5" fill="none"/>
                  </svg>
                </div>
              )}
              <div
                className={`max-w-[80%] px-3 py-2 rounded-2xl text-[13px] leading-relaxed whitespace-pre-wrap
                  ${m.role === 'user'
                    ? 'bg-blue-deep text-white rounded-br-sm'
                    : 'bg-ice border border-steel text-[#1a2730] rounded-bl-sm'
                  }`}
              >
                {m.content}
              </div>
            </div>
          ))}

          {chatMutation.isPending && (
            <div className="flex justify-start">
              <div className="w-5 h-5 rounded-full bg-blue flex items-center justify-center mr-1.5 flex-shrink-0 mt-0.5">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="white">
                  <circle cx="12" cy="12" r="4"/>
                </svg>
              </div>
              <div className="bg-ice border border-steel rounded-2xl rounded-bl-sm px-3 py-2">
                <div className="flex gap-1 items-center h-4">
                  {[0, 1, 2].map(i => (
                    <div
                      key={i}
                      className="w-1.5 h-1.5 rounded-full bg-blue-mid"
                      style={{ animation: `bounce 0.8s ${i * 0.15}s infinite` }}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* confirm plan banner */}
        {pendingPlan && (
          <div className="mx-4 mb-2 px-3 py-2.5 bg-[#EAF5EE] border border-[#A8D8B0] rounded-xl flex items-center justify-between gap-2 flex-shrink-0">
            <div>
              <div className="text-[12px] font-medium text-[#2D7A46]">{t('planReady')}</div>
              <div className="text-[11px] text-[#5CB87A]">
                {pendingPlan.length} {t('subtasks')}
              </div>
            </div>
            <button
              onClick={() => confirmMutation.mutate()}
              disabled={confirmMutation.isPending}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-white bg-[#4CAF70] disabled:opacity-60"
            >
              {confirmMutation.isPending ? t('confirming') : t('confirmPlan')}
            </button>
          </div>
        )}

        {/* input bar */}
        <div className="border-t border-ice2 px-3 py-2.5 flex gap-2 items-center flex-shrink-0 pb-safe">
          <input
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder={t('chatPlaceholder')}
            className="flex-1 px-3 py-2 rounded-xl border border-steel text-[13px] bg-ice outline-none focus:border-blue-mid"
          />
          <button
            onClick={send}
            disabled={chatMutation.isPending || !input.trim()}
            className="w-8 h-8 rounded-full bg-blue-deep flex items-center justify-center disabled:opacity-40 flex-shrink-0"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
          </button>
        </div>
      </div>

      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-4px); }
        }
      `}</style>
    </div>
  )
}
