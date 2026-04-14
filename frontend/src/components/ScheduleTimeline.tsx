import { useState, useRef, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import type { ScheduleBlock, UnscheduledTask } from '../api/types'

interface Props {
  blocks: ScheduleBlock[]
  unscheduled?: UnscheduledTask[]
  scheduleGen?: number
  isStreaming?: boolean
  onOpenChat?: (block: ScheduleBlock) => void
}

// ── Urgency helpers ──────────────────────────────────────────────────────────

const _TODAY = new Date().toISOString().slice(0, 10)

/** 0 = no/distant deadline → light; 4 = overdue/today → deepest */
function urgencyLevel(deadline: string | null): 0 | 1 | 2 | 3 | 4 {
  if (!deadline) return 0
  const days = Math.floor(
    (new Date(deadline).getTime() - new Date(_TODAY).getTime()) / 86_400_000
  )
  if (days < 0) return 4
  if (days === 0) return 4
  if (days <= 2) return 3
  if (days <= 5) return 2
  if (days <= 7) return 1
  return 0
}

// Blue palette for scheduled blocks (light → deep as urgency increases)
const BLUE_STRIPE = ['#A8CCDF', '#7AAFD4', '#4E8BB5', '#2E6BA3', '#1A4A72']
const BLUE_TITLE  = ['#3a4450', '#2B5070', '#1F5C8A', '#1A4472', '#0D2F4A']
const BLUE_SUB    = ['#7AAFD4', '#5897C8', '#4E8BB5', '#2E6BA3', '#1A4A72']

// Amber palette for suggested blocks (light → deep as urgency increases)
const AMBER_STRIPE = ['#E8B86D', '#D4943A', '#B57628', '#8B4A0E', '#6B3208']
const AMBER_TITLE  = ['#6B4210', '#5A3510', '#4A2808', '#3A1C04', '#2A1002']
const AMBER_SUB    = ['#D4943A', '#C07828', '#A65F18', '#8B4A0E', '#6B3208']

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(iso: string) {
  const d = new Date(iso)
  const h = d.getHours(), m = d.getMinutes()
  const hh = h % 12 || 12
  const mm = m ? `:${String(m).padStart(2, '0')}` : ''
  return `${hh}${mm}${h < 12 ? 'am' : 'pm'}`
}

function blockHour(iso: string) {
  const d = new Date(iso)
  const h = d.getHours()
  return `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`
}

function durationMins(start: string, end: string) {
  return Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60000)
}

// ── Instant card ─────────────────────────────────────────────────────────────

function InstantCard({ block, onDone }: { block: ScheduleBlock; onDone: () => void }) {
  const [done, setDone] = useState(false)
  // has_explicit_time=false means the reminder has a date only (no specific time)
  const timeLabel = block.has_explicit_time ? blockHour(block.start) : ''
  return (
    <div className="flex gap-2 items-center">
      <div className="w-10 flex-shrink-0 text-[10px] text-gray-text text-right leading-tight">
        {timeLabel}
      </div>
      <div className="w-3.5 flex-shrink-0 flex flex-col items-center">
        <div className="w-1.5 h-1.5 rounded-full flex-shrink-0 mt-[3px] bg-[#C8D4DC]" />
      </div>
      <button
        onClick={() => { setDone(true); onDone() }}
        className={`flex-1 my-0.5 flex items-center gap-2 px-3 py-2 rounded-xl border transition-all
          ${done
            ? 'bg-[#F0FAF3] border-[#A8D8B0] opacity-60'
            : 'bg-white border-gray-border active:bg-ice'
          }`}
      >
        {/* checkbox */}
        <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0
          ${done ? 'border-[#5CB87A] bg-[#5CB87A]' : 'border-[#B8C4CF]'}`}>
          {done && (
            <svg width="8" height="6" viewBox="0 0 8 6" fill="none">
              <path d="M1 3l2 2 4-4" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </div>
        <span className={`text-[12px] flex-1 text-left ${done ? 'line-through text-gray-text' : 'text-[#3a4450]'}`}>
          {block.title}
        </span>
        {block.notes && (
          <span className="text-[10px] text-gray-text">{block.notes}</span>
        )}
      </button>
    </div>
  )
}

// ── Pomodoro dots display ─────────────────────────────────────────────────────

function PomodoroDots({ count, focusMin }: { count: number; focusMin: number }) {
  return (
    <div className="flex items-center gap-1">
      {Array.from({ length: Math.min(count, 6) }).map((_, i) => (
        <div key={i} className="flex items-center gap-0.5">
          <div className="w-1.5 h-1.5 rounded-full bg-blue-mid" />
          {i < count - 1 && <div className="w-2 h-px bg-steel" />}
        </div>
      ))}
      {count > 6 && <span className="text-[9px] text-gray-text ml-0.5">+{count - 6}</span>}
      <span className="text-[10px] text-gray-text ml-1">{count} × {focusMin}m</span>
    </div>
  )
}

// ── Swipeable + full block card ───────────────────────────────────────────────

interface BlockCardProps {
  block: ScheduleBlock
  displayStart: string   // cascaded local start (naive ISO)
  displayEnd: string     // cascaded local end   (naive ISO)
  pomCount: number
  onPomChange: (count: number) => void
  onAccept?: () => void
  onDecline?: () => void
  onOpenChat?: () => void
  /** Extra top margin in px derived from time gap above — animated on pom change */
  gapMargin?: number
}

function BlockCard({ block, displayStart, displayEnd, pomCount, onPomChange, onAccept, onDecline, onOpenChat, gapMargin = 0 }: BlockCardProps) {
  const { t } = useTranslation()
  const type = block.block_type

  // Swipe-to-sync state
  const [offset, setOffset] = useState(0)
  const [synced, setSynced] = useState(false)
  const [isSwiping, setIsSwiping] = useState(false)
  const touchStartX = useRef(0)
  const mouseStartX = useRef(0)
  const isDragging = useRef(false)

  const focusMin = block.focus_minutes ?? 25
  const breakMin = block.break_minutes ?? 5
  const totalMin = pomCount * focusMin + Math.max(0, pomCount - 1) * breakMin

  // Urgency color level (0–4) drives stripe/title depth
  const urgency = urgencyLevel(block.deadline ?? null)

  // Swipe handlers
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (synced || type === 'fixed' || type === 'meal') return
    touchStartX.current = e.touches[0].clientX
    setIsSwiping(true)
  }, [synced, type])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!isSwiping) return
    const delta = e.touches[0].clientX - touchStartX.current
    setOffset(Math.max(-90, Math.min(0, delta)))
  }, [isSwiping])

  const handleTouchEnd = useCallback(() => {
    setIsSwiping(false)
    if (offset < -55) {
      setSynced(true)
      setOffset(0)
    } else {
      setOffset(0)
    }
  }, [offset])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (synced || type === 'fixed' || type === 'meal') return
    mouseStartX.current = e.clientX
    isDragging.current = true
  }, [synced, type])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDragging.current) return
    const delta = e.clientX - mouseStartX.current
    setOffset(Math.max(-90, Math.min(0, delta)))
  }, [])

  const handleMouseUp = useCallback(() => {
    if (!isDragging.current) return
    isDragging.current = false
    if (offset < -55) {
      setSynced(true)
      setOffset(0)
    } else {
      setOffset(0)
    }
  }, [offset])

  // ── Colors ─────────────────────────────────────────────────────────────────
  // Fixed: always neutral grey
  // Synced: green
  // Scheduled: blue scaled by urgency (light → deep)
  // Suggested: amber scaled by urgency (light → deep)

  const cardCls = type === 'fixed'
    ? 'bg-white border border-gray-border'
    : type === 'meal'
    ? 'bg-[#F0FAF4] border border-[#B8DBBF]'
    : type === 'suggested'
    ? 'bg-amber-bg border-2 border-dashed border-amber-border'
    : synced
    ? 'bg-[#F0FAF3] border border-[#A8D8B0]'
    : 'bg-ice border border-steel'

  const titleColor = type === 'fixed'
    ? '#3a4450'
    : type === 'meal'
    ? '#2D6A3F'
    : type === 'suggested'
    ? AMBER_TITLE[urgency]
    : synced
    ? '#2D7A46'
    : BLUE_TITLE[urgency]

  const subColor = type === 'fixed'
    ? '#8A9BA8'
    : type === 'meal'
    ? '#5CA87A'
    : type === 'suggested'
    ? AMBER_SUB[urgency]
    : synced
    ? '#5CB87A'
    : BLUE_SUB[urgency]

  const stripeColor = type === 'fixed'
    ? '#C8D4DC'
    : type === 'meal'
    ? '#7BC28A'
    : type === 'suggested'
    ? AMBER_STRIPE[urgency]
    : synced
    ? '#5CB87A'
    : BLUE_STRIPE[urgency]

  const dotColor = stripeColor
  const stemCls = type === 'fixed'
    ? 'bg-[#E4E8EC]'
    : type === 'meal'
    ? 'bg-[#C4E2C8]'
    : type === 'suggested'
    ? 'bg-amber-border'
    : synced
    ? 'bg-[#B0DDB8]'
    : 'bg-steel'

  const isScheduled = type === 'scheduled' || type === 'suggested'

  return (
    <div
      className="flex gap-2 items-stretch select-none"
      style={{ marginTop: gapMargin, transition: 'margin-top 0.25s cubic-bezier(0.4,0,0.2,1)' }}
    >
      {/* time label */}
      <div className="w-10 flex-shrink-0 text-[10px] text-gray-text pt-4 text-right leading-tight">
        {blockHour(displayStart)}
      </div>
      {/* axis */}
      <div className="w-3.5 flex-shrink-0 flex flex-col items-center">
        <div className="w-2 h-2 rounded-full flex-shrink-0 mt-[17px]" style={{ background: dotColor }} />
        <div className={`w-px flex-1 min-h-1.5 ${stemCls}`} />
      </div>

      {/* swipe wrapper */}
      <div className="flex-1 my-1.5 relative overflow-hidden rounded-xl">
        {/* sync reveal background */}
        {!synced && type !== 'fixed' && type !== 'meal' && (
          <div
            className="absolute inset-0 bg-[#4CAF70] rounded-xl flex items-center justify-end pr-4"
            style={{ opacity: Math.min(1, Math.abs(offset) / 55) }}
          >
            <div className="flex flex-col items-center gap-0.5">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              <span className="text-[9px] text-white font-medium">{t('sync')}</span>
            </div>
          </div>
        )}
        {/* synced badge */}
        {synced && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <span className="text-[10px] text-[#5CB87A] font-medium">✓ {t('synced')}</span>
          </div>
        )}

        {/* card face */}
        <div
          className={`rounded-xl px-3 py-2.5 relative overflow-hidden cursor-grab active:cursor-grabbing ${cardCls}`}
          style={{
            transform: `translateX(${offset}px)`,
            transition: isSwiping || isDragging.current ? 'none' : 'transform 0.25s ease',
          }}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <div className="absolute left-0 top-0 bottom-0 w-0.5" style={{ background: stripeColor }} />

          {/* phase label */}
          {block.phase_label && (
            <div className="mb-1">
              <span className="text-[9px] font-medium bg-ice2 border border-steel rounded-full px-2 py-0.5"
                style={{ color: subColor }}>
                {block.phase_label}
              </span>
            </div>
          )}

          {/* title row */}
          <div className="flex items-start justify-between gap-1">
            <div className="text-[13px] font-medium flex-1" style={{ color: titleColor }}>{block.title}</div>
            {/* ★ uncertain / planning chat button */}
            {isScheduled && (
              <button
                onMouseDown={e => e.stopPropagation()}
                onTouchStart={e => e.stopPropagation()}
                onClick={onOpenChat}
                className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-full hover:bg-ice2 transition-colors"
                title={t('planningChat')}
              >
                <span className={`text-[11px] ${block.is_uncertain ? 'text-amber' : 'text-gray-text'}`}>
                  {block.is_uncertain ? '★' : '⋯'}
                </span>
              </button>
            )}
          </div>

          {/* time + cognitive load — cascaded from ScheduleTimeline */}
          <div className="text-[11px] leading-relaxed mt-0.5" style={{ color: subColor }}>
            {fmt(displayStart)} – {fmt(displayEnd)}
            {block.cognitive_load && ` · ${t(block.cognitive_load)}`}
          </div>

          {/* Pomodoro controls — only for agent/suggested blocks */}
          {isScheduled && !synced && (
            <div className="flex items-center justify-between mt-2">
              <PomodoroDots count={pomCount} focusMin={focusMin} />
              <div className="flex items-center gap-1">
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onTouchStart={e => e.stopPropagation()}
                  onClick={() => onPomChange(Math.max(1, pomCount - 1))}
                  disabled={pomCount <= 1}
                  className="w-5 h-5 rounded-full border border-steel text-[12px] text-gray-text flex items-center justify-center disabled:opacity-30 hover:bg-ice2"
                >
                  −
                </button>
                <span className="text-[10px] text-gray-text w-7 text-center">{totalMin}m</span>
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onTouchStart={e => e.stopPropagation()}
                  onClick={() => onPomChange(pomCount + 1)}
                  className="w-5 h-5 rounded-full border border-steel text-[12px] text-gray-text flex items-center justify-center hover:bg-ice2"
                >
                  +
                </button>
              </div>
            </div>
          )}

          {/* Accept / Keep buttons for suggested blocks */}
          {type === 'suggested' && (
            <div className="flex gap-1.5 mt-2">
              <button
                onMouseDown={e => e.stopPropagation()}
                onTouchStart={e => e.stopPropagation()}
                onClick={onAccept}
                className="flex-1 py-1.5 rounded-lg text-[11px] font-medium bg-blue-mid text-white border border-blue-mid"
              >
                {t('moveTo', { time: fmt(block.start) })}
              </button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onTouchStart={e => e.stopPropagation()}
                onClick={onDecline}
                className="flex-1 py-1.5 rounded-lg text-[11px] font-medium text-gray-text border border-gray-border bg-transparent"
              >
                {t('keepOriginal')}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Stable-key helpers + localStorage persistence ────────────────────────────

/**
 * Stable identity for a block that survives regeneration.
 * task_id is the Reminders/manual ID (stable). For fixed blocks with no task_id
 * we fall back to start+title (those don't have pomCounts anyway).
 */
function blockKey(block: ScheduleBlock): string {
  return block.task_id ? `${block.task_id}::${block.title}` : `${block.start}::${block.title}`
}

const LS_POM    = 'dayflow_pom_counts'
const LS_DIMISS = 'dayflow_dismissed'
const LS_ACCEPT = 'dayflow_accepted'

function lsLoad<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback }
  catch { return fallback }
}
function lsSave(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch {}
}

// ── Cascade helpers ───────────────────────────────────────────────────────────

const BUFFER_MINUTES = 10

/** Build a naive local ISO string (no Z) from a Date — avoids UTC shift in fmt(). */
function toLocalISO(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00`
}

function effectiveMin(block: ScheduleBlock, pomCounts: Record<string, number>): number {
  const count = pomCounts[blockKey(block)] ?? (block.pomodoro_count ?? 1)
  const focusMin = block.focus_minutes ?? 25
  const breakMin = block.break_minutes ?? 5
  return count * focusMin + Math.max(0, count - 1) * breakMin
}

/**
 * Recompute displayed start/end for every regular (non-instant) block.
 * Fixed blocks are immovable anchors; scheduled blocks cascade after them.
 * Display end is capped at the next fixed block's start so we never visually overlap a class/meeting.
 * gapMargin is proportional to the idle time before each block (for push animation).
 */
function cascadeBlocks(
  blocks: ScheduleBlock[],
  pomCounts: Record<string, number>,
): Array<{ block: ScheduleBlock; displayStart: string; displayEnd: string; gapMargin: number }> {
  // Sort by backend start time so fixed blocks always act as anchors in the
  // correct position, even when intermediate stream states arrive out of order.
  const sorted = [...blocks].sort(
    (a, b) => new Date(a.start).getTime() - new Date(b.start).getTime()
  )

  let cursor: Date | null = null
  let prevDisplayEnd: Date | null = null

  // Pre-compute sorted fixed + meal block start times for cap lookup
  const fixedStarts = sorted
    .filter(b => b.block_type === 'fixed' || b.block_type === 'meal')
    .map(b => new Date(b.start))
    .sort((a, b) => a.getTime() - b.getTime())

  return sorted.map(block => {
    if (block.block_type === 'fixed' || block.block_type === 'meal') {
      const ds = new Date(block.start)
      const de = new Date(block.end)
      // Gap margin for fixed blocks based on time since previous block ended
      const gapMins = prevDisplayEnd ? Math.max(0, (ds.getTime() - prevDisplayEnd.getTime()) / 60_000) : 0
      const gapMargin = Math.min(20, gapMins * 0.4)
      cursor = new Date(de.getTime() + BUFFER_MINUTES * 60_000)
      prevDisplayEnd = de
      return { block, displayStart: block.start, displayEnd: block.end, gapMargin }
    }
    const backendStart = new Date(block.start)
    // Don't move earlier than the backend-scheduled start
    const displayStart = cursor && cursor > backendStart ? cursor : backendStart
    const durMin = effectiveMin(block, pomCounts)
    let displayEnd = new Date(displayStart.getTime() + durMin * 60_000)

    // Cap display end at the next fixed block's start to prevent visual overlap
    const nextFixed = fixedStarts.find(fs => fs > displayStart)
    if (nextFixed && displayEnd > nextFixed) {
      displayEnd = nextFixed
    }

    // Gap margin relative to previous block's display end
    const gapMins = prevDisplayEnd ? Math.max(0, (displayStart.getTime() - prevDisplayEnd.getTime()) / 60_000) : 0
    const gapMargin = Math.min(20, gapMins * 0.4)

    cursor = new Date(displayEnd.getTime() + BUFFER_MINUTES * 60_000)
    prevDisplayEnd = displayEnd
    return { block, displayStart: toLocalISO(displayStart), displayEnd: toLocalISO(displayEnd), gapMargin }
  })
}

// ── Main timeline ─────────────────────────────────────────────────────────────

export default function ScheduleTimeline({ blocks, unscheduled = [], scheduleGen, isStreaming, onOpenChat }: Props) {
  const { t } = useTranslation()

  // All three stores use stable blockKey (task_id::title) so they survive regeneration.
  // Initialised from localStorage so customisations persist across refreshes too.
  const [pomCounts, setPomCounts] = useState<Record<string, number>>(
    () => lsLoad(LS_POM, {})
  )
  const [dismissed, setDismissed] = useState<Set<string>>(
    () => new Set<string>(lsLoad<string[]>(LS_DIMISS, []))
  )
  const [accepted, setAccepted] = useState<Set<string>>(
    () => new Set<string>(lsLoad<string[]>(LS_ACCEPT, []))
  )
  const [doneTasks, setDoneTasks] = useState<Set<string>>(new Set())

  // Persist whenever any store changes
  useEffect(() => { lsSave(LS_POM, pomCounts) }, [pomCounts])
  useEffect(() => { lsSave(LS_DIMISS, [...dismissed]) }, [dismissed])
  useEffect(() => { lsSave(LS_ACCEPT, [...accepted]) }, [accepted])

  // Stagger-entry animation: when a new full schedule arrives (scheduleGen bumps),
  // mark all current block keys as "fresh" so they animate in sequentially.
  const lastGenRef = useRef(scheduleGen ?? 0)
  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (scheduleGen !== undefined && scheduleGen !== lastGenRef.current) {
      lastGenRef.current = scheduleGen
      setFreshKeys(new Set(blocks.map(blockKey)))
      // Clear after all stagger animations have finished (~1.5 s)
      const tid = setTimeout(() => setFreshKeys(new Set()), 1500)
      return () => clearTimeout(tid)
    }
  }, [scheduleGen, blocks])

  const visible = blocks
    .filter(b => !doneTasks.has(b.start + b.title))
    .map(b => {
      const k = blockKey(b)
      if (dismissed.has(k)) return { ...b, block_type: 'fixed' as const }
      if (accepted.has(k)) return { ...b, block_type: 'scheduled' as const }
      return b
    })

  const instantBlocks = visible.filter(b => b.block_type === 'instant')
  const regularBlocks = visible.filter(b => b.block_type !== 'instant')

  // Cascaded display times — recomputed whenever pomCounts changes
  const cascaded = cascadeBlocks(regularBlocks, pomCounts)

  return (
    <div className="bg-white border border-gray-border rounded-2xl p-3.5">
      {/* header */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="text-[11px] text-gray-text uppercase tracking-wide">{t('schedule')}</div>
        <div className="flex gap-2.5">
          {(['fixed', 'suggest', 'agent'] as const).map((k, i) => (
            <div key={k} className="flex items-center gap-1 text-[10px] text-gray-text">
              <div className="w-1.5 h-1.5 rounded-full" style={{
                background: i === 0 ? '#B8C4CF' : i === 1 ? '#D4943A' : '#7AAFD4'
              }} />
              {t(k)}
            </div>
          ))}
        </div>
      </div>

      {/* quick actions strip (instant blocks) */}
      {instantBlocks.length > 0 && (
        <div className="mb-3">
          <div className="text-[10px] text-gray-text mb-1.5 flex items-center gap-1">
            <span className="w-1 h-1 rounded-full bg-[#C8D4DC] inline-block" />
            {t('quickActions')}
          </div>
          <div className="flex flex-col gap-1">
            {instantBlocks.map((b, i) => (
              <InstantCard
                key={b.start + i}
                block={b}
                onDone={() => setDoneTasks(s => new Set(s).add(b.start + b.title))}
              />
            ))}
          </div>
          {regularBlocks.length > 0 && <div className="border-t border-ice2 mt-2.5 mb-0.5" />}
        </div>
      )}

      {/* regular timeline */}
      <div className="flex flex-col">
        {cascaded.map(({ block, displayStart, displayEnd, gapMargin }, i) => {
          const bk = blockKey(block)
          const isFresh = freshKeys.has(bk)
          return (
            <div
              key={bk}
              style={isFresh ? {
                animation: `blockEnter 0.35s cubic-bezier(0.4,0,0.2,1) both`,
                animationDelay: `${i * 55}ms`,
              } : undefined}
            >
              <BlockCard
                block={block}
                displayStart={displayStart}
                displayEnd={displayEnd}
                pomCount={pomCounts[bk] ?? (block.pomodoro_count ?? 1)}
                onPomChange={count => setPomCounts(prev => ({ ...prev, [bk]: count }))}
                onAccept={() => setAccepted(s => new Set(s).add(bk))}
                onDecline={() => setDismissed(s => new Set(s).add(bk))}
                onOpenChat={() => onOpenChat?.(block)}
                gapMargin={i === 0 ? 0 : gapMargin}
              />
            </div>
          )
        })}

        {/* streaming skeleton — shown while waiting for scheduled blocks */}
        {isStreaming && regularBlocks.filter(b => b.block_type !== 'fixed' && b.block_type !== 'meal').length === 0 && (
          <div className="flex flex-col gap-0.5 mt-1">
            {[0, 1, 2].map(i => (
              <div key={i} className="flex gap-2 items-stretch" style={{ opacity: 1 - i * 0.25 }}>
                <div className="w-10 flex-shrink-0" />
                <div className="w-3.5 flex-shrink-0 flex flex-col items-center">
                  <div className="w-2 h-2 rounded-full flex-shrink-0 mt-[17px] bg-[#E4E8EC]" />
                  <div className="w-px flex-1 min-h-4 bg-[#E4E8EC]" />
                </div>
                <div className="flex-1 my-1.5 rounded-xl bg-[#EFF3F7]"
                  style={{ height: 62, animation: `skeletonPulse 1.4s ease-in-out ${i * 0.15}s infinite` }} />
              </div>
            ))}
            <div className="text-[11px] text-gray-text text-center mt-2 mb-1 opacity-50" style={{ animation: 'skeletonPulse 1.4s ease-in-out infinite' }}>
              {t('generating')}…
            </div>
          </div>
        )}
      </div>

      {/* swipe hint */}
      {regularBlocks.some(b => b.block_type !== 'fixed' && b.block_type !== 'meal') && (
        <div className="mt-2 text-[10px] text-gray-text text-center opacity-60">
          {t('swipeToSync')}
        </div>
      )}

      {/* unscheduled tasks — tasks that had no available slot today */}
      {unscheduled.length > 0 && (
        <div className="mt-3 pt-2.5 border-t border-ice2">
          <div className="text-[10px] text-gray-text uppercase tracking-wide mb-1.5">
            {t('unscheduled')}
          </div>
          <div className="text-[10px] text-gray-text mb-2 opacity-70">{t('unscheduledSub')}</div>
          <div className="flex flex-col gap-1">
            {unscheduled.map((u, i) => (
              <div key={u.parent_id + i}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-ice border border-ice2">
                <div className="w-1 h-1 rounded-full flex-shrink-0"
                  style={{ background: u.cognitive_load === 'deep' ? '#4E8BB5' : u.cognitive_load === 'medium' ? '#7AAFD4' : '#B8C4CF' }} />
                <span className="text-[12px] text-[#3a4450] flex-1">{u.title}</span>
                <span className="text-[10px] text-gray-text">{t('estMins', { n: u.estimated_minutes })}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes blockEnter {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0);   }
        }
        @keyframes skeletonPulse {
          0%, 100% { opacity: 0.45; }
          50%       { opacity: 0.9;  }
        }
      `}</style>
    </div>
  )
}
