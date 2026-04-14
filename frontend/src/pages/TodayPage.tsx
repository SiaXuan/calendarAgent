import { useState, useRef, useCallback, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { fetchSchedule, generateSchedule, streamSchedule } from '../api/schedule'
import { reclassifyTasks } from '../api/tasks'
import HealthCard from '../components/HealthCard'
import ScheduleTimeline from '../components/ScheduleTimeline'
import SleepInputModal from '../components/SleepInputModal'
import TaskChatModal from '../components/TaskChatModal'
import type { DaySchedule, ScheduleBlock } from '../api/types'

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

export default function TodayPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const date = todayStr()

  const [sleepModalOpen, setSleepModalOpen] = useState(false)
  const [chatBlock, setChatBlock] = useState<ScheduleBlock | null>(null)
  const [reclassifyToast, setReclassifyToast] = useState<string | null>(null)
  // isStreaming = true while an SSE connection is open
  const [isStreaming, setIsStreaming] = useState(false)
  // increments each time a full schedule arrives — triggers block stagger animation
  const [scheduleGen, setScheduleGen] = useState(0)
  const esCleanupRef = useRef<(() => void) | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['schedule', date],
    queryFn: () => fetchSchedule(date),
    retry: false,
  })


  /** Start (or restart) the SSE stream for today's schedule. */
  const startStream = useCallback(() => {
    // Close any existing connection
    esCleanupRef.current?.()
    setIsStreaming(true)

    // Wipe stale scheduled/suggested blocks immediately so the skeleton renders
    // while the new schedule is being generated.  Fixed blocks (classes) stay as
    // visual anchors.
    qc.setQueryData(['schedule', date], (old: DaySchedule | undefined) => {
      if (!old) return old
      return { ...old, blocks: old.blocks.filter(b => b.block_type === 'fixed') }
    })

    const cleanup = streamSchedule(
      date,
      (evt) => {
        if (evt.type === 'health') {
          // Render health card immediately — blocks not ready yet
          qc.setQueryData(['schedule', date], (old: DaySchedule | undefined) => ({
            date,
            energy_curve: evt.energy_curve,
            health_summary: evt.health_summary,
            blocks: old?.blocks ?? [],
            unscheduled: old?.unscheduled ?? [],
          }))
        } else if (evt.type === 'fixed') {
          // Show fixed calendar blocks (class / meetings) before tasks are ready
          qc.setQueryData(['schedule', date], (old: DaySchedule | undefined) => {
            if (!old) return old
            const nonFixed = old.blocks.filter(b => b.block_type !== 'fixed')
            return { ...old, blocks: [...evt.blocks, ...nonFixed] }
          })
        } else if (evt.type === 'schedule') {
          // Full schedule arrived — replace blocks and trigger stagger animation
          console.log('[STREAM] schedule event blocks:', evt.blocks.map(b => `${b.block_type} ${b.start} ${b.title}`))
          qc.setQueryData(['schedule', date], (old: DaySchedule | undefined) => ({
            ...(old ?? { date, energy_curve: [], health_summary: '' }),
            blocks: evt.blocks,
            unscheduled: evt.unscheduled,
          }))
          setScheduleGen(g => g + 1)
          setIsStreaming(false)
        }
      },
      () => setIsStreaming(false),
    )
    esCleanupRef.current = cleanup
  }, [date, qc])

  // Auto-stream when no schedule exists yet (e.g. after server restart clears schedule_store)
  const autoStartedRef = useRef(false)
  useEffect(() => {
    if (isError && !data && !isStreaming && !autoStartedRef.current) {
      autoStartedRef.current = true
      startStream()
    }
  }, [isError, data, isStreaming, startStream])

  const reclassify = useMutation({
    mutationFn: async () => {
      const result = await reclassifyTasks()
      return generateSchedule(date).then(d => ({ schedule: d, count: result.reclassified }))
    },
    onSuccess: ({ schedule, count }) => {
      qc.setQueryData(['schedule', date], schedule)
      setScheduleGen(g => g + 1)
      setReclassifyToast(t('reclassified', { count }))
      setTimeout(() => setReclassifyToast(null), 2500)
    },
  })

  const dateLabel = new Date().toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="flex flex-col h-full">
      {/* topbar */}
      <div className="h-13 bg-[#F7FAFE] border-b border-ice2 flex items-center justify-between px-5 flex-shrink-0">
        <span className="text-[17px] font-medium text-[#1a2730]">
          {t('today')} · {dateLabel}
        </span>
        <button
          onClick={startStream}
          disabled={isStreaming}
          className="w-8 h-8 rounded-full bg-ice border border-steel flex items-center justify-center"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none"
            stroke="#4E8BB5" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
            style={{ animation: isStreaming ? 'spin 1s linear infinite' : 'none' }}>
            <path d="M4 4v5h5M20 20v-5h-5"/>
            <path d="M20 9A8 8 0 006.93 4.93M4 15a8 8 0 0013.07 4.07"/>
          </svg>
        </button>
      </div>

      {/* scrollable content */}
      <div className="flex-1 overflow-y-auto bg-ice p-3 flex flex-col gap-2.5">
        {isLoading && !data && (
          <div className="flex-1 flex items-center justify-center text-[13px] text-gray-text">{t('loading')}</div>
        )}
        {isError && !data && (
          <div className="bg-white border border-gray-border rounded-2xl p-4 text-center">
            <div className="text-[13px] text-gray-text mb-3">{t('noSchedule')}</div>
            <button onClick={startStream} className="text-[13px] font-medium text-blue-deep">
              {t('regenerate')}
            </button>
          </div>
        )}
        {data && (
          <>
            <HealthCard
              healthSummary={data.health_summary}
              energyCurve={data.energy_curve}
              onLogSleep={() => setSleepModalOpen(true)}
            />
            <ScheduleTimeline
              blocks={data.blocks}
              unscheduled={data.unscheduled}
              scheduleGen={scheduleGen}
              isStreaming={isStreaming}
              onOpenChat={block => {
                if (block.task_id) setChatBlock(block)
              }}
            />
            <div className="flex gap-2 mb-3">
              <button
                onClick={startStream}
                disabled={isStreaming || reclassify.isPending}
                className="flex-1 p-3.5 rounded-2xl bg-white border border-steel text-[13px] font-medium text-blue-deep text-center active:bg-ice2 disabled:opacity-50"
              >
                {isStreaming ? '…' : t('regenerate')}
              </button>
              <button
                onClick={() => reclassify.mutate()}
                disabled={reclassify.isPending || isStreaming}
                className="flex-1 p-3.5 rounded-2xl bg-white border border-steel text-[13px] font-medium text-blue-deep text-center active:bg-ice2 disabled:opacity-50"
              >
                {reclassify.isPending ? '…' : t('reclassify')}
              </button>
            </div>
          </>
        )}
      </div>

      {/* sleep input modal */}
      {sleepModalOpen && (
        <SleepInputModal onClose={() => setSleepModalOpen(false)} />
      )}

      {/* reclassify toast */}
      {reclassifyToast && (
        <div className="absolute left-1/2 z-30 rounded-full px-4 py-2 text-[12px] font-medium text-white whitespace-nowrap pointer-events-none"
          style={{ bottom: 76, background: 'rgba(26,74,114,0.92)', transform: 'translateX(-50%)' }}>
          {reclassifyToast}
        </div>
      )}

      {/* task chat modal */}
      {chatBlock && (
        <TaskChatModal
          block={chatBlock}
          onClose={() => setChatBlock(null)}
          onConfirmed={() => {
            setChatBlock(null)
            startStream()
          }}
        />
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
