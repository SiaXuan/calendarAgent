import { useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  confirmProposal,
  sendAgentMessage,
  type AgentChatResult,
  type AgentProposal,
} from '../api/agent_chat'

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  terminal?: AgentChatResult['terminal_state']
  proposal?: AgentProposal | null
  proposalResolved?: boolean
}

const STARTERS = [
  '我下午有点累，把深度任务挪到上午',
  '周五前帮我安排好写作的时间',
  '今天太满了，看看能不能精简',
]


export default function ChatPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const date = todayStr()

  // Messages live in the react-query cache (app-level) instead of component
  // state, so switching tabs (which unmounts ChatPage) doesn't wipe the
  // conversation. The backend also keeps history; this is the display copy.
  const { data: messages = [] } = useQuery<Msg[]>({
    queryKey: ['chatMessages', date],
    queryFn: async () => qc.getQueryData<Msg[]>(['chatMessages', date]) ?? [],
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  })
  const setMessages = (updater: (m: Msg[]) => Msg[]) =>
    qc.setQueryData<Msg[]>(['chatMessages', date], (old) => updater(old ?? []))

  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const refreshSchedule = () => qc.invalidateQueries({ queryKey: ['schedule', date] })

  const send = useMutation({
    mutationFn: (text: string) => {
      const ac = new AbortController()
      abortRef.current = ac
      return sendAgentMessage(date, text, ac.signal)
    },
    onSuccess: (res) => {
      setMessages(m => [...m, {
        role: 'assistant',
        content: res.message,
        terminal: res.terminal_state,
        proposal: res.proposal,
      }])
      if (res.terminal_state === 'success') refreshSchedule()
    },
    onError: (err: unknown) => {
      // User-initiated abort → no error bubble, just a quiet note.
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(m => [...m, { role: 'assistant', content: '已停止。', terminal: 'no_change' }])
        return
      }
      setMessages(m => [...m, {
        role: 'assistant',
        content: '出错了，稍后再试。',
        terminal: 'degraded',
      }])
    },
    onSettled: () => { abortRef.current = null },
  })

  const stop = () => abortRef.current?.abort()

  const confirm = useMutation({
    mutationFn: () => confirmProposal(date),
    onSuccess: (res) => {
      setMessages(m => {
        const next = m.map(msg => msg.proposal ? { ...msg, proposalResolved: true } : msg)
        return [...next, { role: 'assistant' as const, content: res.message, terminal: res.terminal_state }]
      })
      if (res.terminal_state === 'success') refreshSchedule()
    },
  })

  const doSend = (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || send.isPending) return
    setMessages(m => [...m, { role: 'user', content: trimmed }])
    setInput('')
    send.mutate(trimmed)
  }

  const rejectProposal = () => {
    setMessages(m => [
      ...m.map(msg => msg.proposal ? { ...msg, proposalResolved: true } : msg),
      { role: 'assistant', content: '好的，保持原样。', terminal: 'no_change' },
    ])
  }

  return (
    <div className="flex-1 flex flex-col bg-ice min-h-0">
      {/* topbar */}
      <div className="h-13 bg-[#F7FAFE] border-b border-ice2 flex items-center px-5 flex-shrink-0">
        <span className="text-[17px] font-medium text-[#1a2730]">{t('chat')}</span>
      </div>

      {/* messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col gap-2 mt-4">
            <p className="text-[13px] text-gray-text text-center mb-1">{t('chatAgentHint')}</p>
            {STARTERS.map((s, i) => (
              <button
                key={i}
                onClick={() => doSend(s)}
                className="text-[13px] text-blue-deep bg-white border border-steel rounded-xl px-3.5 py-2.5 text-left active:bg-ice"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className="max-w-[85%] flex flex-col gap-2">
              <div
                className={
                  m.role === 'user'
                    ? 'bg-blue-deep text-white rounded-2xl rounded-br-sm px-3.5 py-2.5 text-[14px] whitespace-pre-wrap'
                    : `${m.terminal === 'degraded' ? 'bg-amber-bg border-amber-border' : 'bg-white border-steel'} border rounded-2xl rounded-bl-sm px-3.5 py-2.5 text-[14px] text-[#1a2730] whitespace-pre-wrap`
                }
              >
                {m.content}
              </div>

              {/* Proposal preview card */}
              {m.proposal && !m.proposalResolved && (
                <ProposalCard
                  proposal={m.proposal}
                  pending={confirm.isPending}
                  onConfirm={() => confirm.mutate()}
                  onReject={rejectProposal}
                />
              )}
            </div>
          </div>
        ))}

        {send.isPending && (
          <div className="flex justify-start">
            <div className="bg-white border border-steel rounded-2xl rounded-bl-sm px-4 py-3">
              <span className="inline-flex gap-1">
                <span className="w-1.5 h-1.5 bg-steel rounded-full animate-bounce" />
                <span className="w-1.5 h-1.5 bg-steel rounded-full animate-bounce" style={{ animationDelay: '0.15s' }} />
                <span className="w-1.5 h-1.5 bg-steel rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
              </span>
            </div>
          </div>
        )}
      </div>

      {/* input */}
      <div className="flex-shrink-0 border-t border-ice2 bg-[#F7FAFE] px-3 py-2.5 flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            // Don't send while an IME is composing — pressing Enter to pick a
            // candidate (common with Chinese/Japanese input) must not submit.
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) doSend(input)
          }}
          placeholder={t('chatAgentPlaceholder')}
          className="flex-1 bg-white border border-gray-border rounded-xl px-3.5 py-2.5 text-[14px] outline-none focus:border-steel"
        />
        {send.isPending ? (
          <button
            onClick={stop}
            className="bg-[#B5524E] text-white rounded-xl px-4 text-[14px]"
          >
            {t('chatAgentStop')}
          </button>
        ) : (
          <button
            onClick={() => doSend(input)}
            disabled={!input.trim()}
            className="bg-blue-deep text-white rounded-xl px-4 text-[14px] disabled:opacity-40"
          >
            {t('chatAgentSend')}
          </button>
        )}
      </div>
    </div>
  )
}


function ProposalCard({ proposal, pending, onConfirm, onReject }: {
  proposal: AgentProposal
  pending: boolean
  onConfirm: () => void
  onReject: () => void
}) {
  const { t } = useTranslation()
  const opLabel: Record<string, string> = {
    move: t('proposalMove'), remove: t('proposalRemove'), add: t('proposalAdd'),
  }
  return (
    <div className="bg-white border-2 rounded-xl p-3 flex flex-col gap-2" style={{ borderColor: '#E8C07A' }}>
      {/* Unmistakable "not applied yet" banner — agent's message may say "done",
          but nothing changes until the user taps Apply. */}
      <div className="text-[12px] font-medium px-2 py-1.5 rounded" style={{ background: '#FDF4E8', color: '#9A6B1E' }}>
        ⏳ {t('proposalPending')}
      </div>
      <div className="text-[11px] text-gray-text uppercase tracking-wide">{t('proposalTitle')}</div>
      <ul className="flex flex-col gap-1.5">
        {proposal.changes.map((c, i) => {
          const timing =
            c.op === 'move' ? `${c.from_time ?? ''} → ${c.to_time ?? ''}`
            : c.op === 'remove' ? c.from_time ?? ''
            : c.to_time ?? ''
          return (
            <li key={i} className="text-[12px] text-[#1a2730] flex flex-col gap-0.5">
              <div className="flex items-center gap-1.5">
                <span
                  className="text-[10px] px-1.5 py-px rounded shrink-0"
                  style={{
                    background: c.op === 'remove' ? '#FBE9E9' : '#E8F0F8',
                    color: c.op === 'remove' ? '#B5524E' : '#2A6090',
                  }}
                >
                  {opLabel[c.op] ?? c.op}
                </span>
                {timing && <span className="text-blue-mid text-[11px] shrink-0">{timing}</span>}
              </div>
              <span className="leading-snug break-words">{c.title}</span>
            </li>
          )
        })}
      </ul>
      <div className="flex gap-2 mt-1">
        <button
          onClick={onConfirm}
          disabled={pending}
          className="flex-1 bg-blue-deep text-white text-[13px] rounded-lg py-2 disabled:opacity-50"
        >
          {t('proposalConfirm')}
        </button>
        <button
          onClick={onReject}
          disabled={pending}
          className="flex-1 bg-ice text-gray-text text-[13px] rounded-lg py-2 border border-gray-border"
        >
          {t('proposalReject')}
        </button>
      </div>
    </div>
  )
}
