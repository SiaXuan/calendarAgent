import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { postHealth, getHealth } from '../api/health'
import { generateSchedule } from '../api/schedule'

interface Props {
  onClose: () => void
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

/** Extract HH:MM from an ISO datetime string like "2026-04-12T03:00:00" */
function toTimeStr(isoDatetime: string): string {
  return isoDatetime.slice(11, 16)  // "03:00"
}

/** Fallback defaults used only when no saved health data exists */
function defaultTimes() {
  return { sleepTime: '23:00', wakeTime: '07:30' }
}

/**
 * Format a naive local datetime string — no UTC conversion.
 * Backend treats datetimes as local time, so we must NOT use toISOString() here.
 */
function toLocalDatetime(dateStr: string, timeStr: string): string {
  return `${dateStr}T${timeStr}:00`
}

/** Subtract one calendar day from a YYYY-MM-DD string using local date arithmetic. */
function subtractOneDay(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  const prev = new Date(y, m - 1, d - 1) // local constructor — no UTC shift
  return [
    prev.getFullYear(),
    String(prev.getMonth() + 1).padStart(2, '0'),
    String(prev.getDate()).padStart(2, '0'),
  ].join('-')
}

function durationLabel(sleep: string, wake: string): string {
  const [sh, sm] = sleep.split(':').map(Number)
  const [wh, wm] = wake.split(':').map(Number)
  let sleepMins = sh * 60 + sm
  let wakeMins = wh * 60 + wm
  // If sleep is in the early hours (e.g. 03:00) and wake is later same day → no adjustment needed
  // If sleep >= wake in minutes → overnight (e.g. 23:00 sleep, 07:30 wake)
  if (sleepMins > wakeMins) wakeMins += 24 * 60
  const diff = wakeMins - sleepMins
  const h = Math.floor(diff / 60)
  const m = diff % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

export default function SleepInputModal({ onClose }: Props) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const today = todayStr()
  const { sleepTime: defaultSleep, wakeTime: defaultWake } = defaultTimes()

  const [sleepTime, setSleepTime] = useState(defaultSleep)
  const [wakeTime, setWakeTime] = useState(defaultWake)
  const [hr, setHr] = useState('')
  const [hrv, setHrv] = useState('')
  const [steps, setSteps] = useState('')
  const [activeMin, setActiveMin] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [done, setDone] = useState(false)

  // Pre-populate from last saved health data if available
  useEffect(() => {
    getHealth(today).then(snapshot => {
      setSleepTime(toTimeStr(snapshot.sleep.sleep_start))
      setWakeTime(toTimeStr(snapshot.sleep.sleep_end))
      if (snapshot.resting_heart_rate) setHr(String(snapshot.resting_heart_rate))
      if (snapshot.hrv) setHrv(String(snapshot.hrv))
      if (snapshot.steps) setSteps(String(snapshot.steps))
      if (snapshot.active_minutes) setActiveMin(String(snapshot.active_minutes))
    }).catch(() => {
      // No data yet — keep defaults
    })
  }, [today])

  // Determine sleep date:
  // - If bedtime hour < 12 (i.e. early morning like 01:00, 03:00) → same calendar day as wake
  // - If bedtime hour >= 12 (e.g. 23:00) → previous calendar day
  const [sh] = sleepTime.split(':').map(Number)
  const sleepDateStr = sh < 12 ? today : subtractOneDay(today)

  const submitMutation = useMutation({
    mutationFn: async () => {
      const sleep_start = toLocalDatetime(sleepDateStr, sleepTime)
      const sleep_end = toLocalDatetime(today, wakeTime)
      await postHealth({
        date: today,
        sleep_start,
        sleep_end,
        resting_heart_rate: hr ? parseInt(hr) : undefined,
        hrv: hrv ? parseFloat(hrv) : undefined,
        steps: steps ? parseInt(steps) : undefined,
        active_minutes: activeMin ? parseInt(activeMin) : undefined,
      })
      // Regenerate schedule with new health data
      await generateSchedule(today)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['schedule', today] })
      setDone(true)
      setTimeout(onClose, 800)
    },
  })

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      style={{ background: 'rgba(15,28,44,0.4)', backdropFilter: 'blur(2px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-t-2xl px-5 pt-4 pb-8">
        {/* drag handle */}
        <div className="flex justify-center mb-4">
          <div className="w-8 h-1 rounded-full bg-[#D8DDE3]" />
        </div>

        {/* header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-[15px] font-semibold text-[#1a2730]">{t('morningCheckIn')}</div>
            <div className="text-[12px] text-gray-text mt-0.5">{t('morningCheckInSub')}</div>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-full bg-ice flex items-center justify-center"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#8A939E" strokeWidth="2.5">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>

        {/* sleep / wake pickers */}
        <div className="flex gap-3 mb-3">
          <div className="flex-1">
            <label className="text-[11px] text-gray-text block mb-1">{t('fellAsleep')}</label>
            <input
              type="time"
              value={sleepTime}
              onChange={e => setSleepTime(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid"
            />
          </div>
          <div className="flex-1">
            <label className="text-[11px] text-gray-text block mb-1">{t('wokeUp')}</label>
            <input
              type="time"
              value={wakeTime}
              onChange={e => setWakeTime(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid"
            />
          </div>
        </div>

        {/* duration display */}
        <div className="text-center mb-4">
          <span className="text-[12px] text-gray-text">{t('sleepDuration')} </span>
          <span className="text-[13px] font-medium text-blue-deep">
            {durationLabel(sleepTime, wakeTime)}
          </span>
        </div>

        {/* Apple Watch data — collapsible */}
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="w-full flex items-center justify-between py-2 text-[12px] text-gray-text mb-1"
        >
          <span className="flex items-center gap-1.5">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8A939E" strokeWidth="1.8">
              <rect x="5" y="2" width="14" height="20" rx="3"/>
              <path d="M9 2v2M15 2v2M9 20v2M15 20v2M12 8v4l2 2"/>
            </svg>
            {t('appleWatchOptional')}
          </span>
          <svg
            width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            style={{ transform: showAdvanced ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}
          >
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>

        {showAdvanced && (
          <div className="mb-3">
            <div className="grid grid-cols-2 gap-2.5 mb-2.5">
              <div>
                <label className="text-[11px] text-gray-text block mb-1">{t('restingHR')} (bpm)</label>
                <input type="number" value={hr} onChange={e => setHr(e.target.value)}
                  placeholder="58" min={30} max={120}
                  className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid" />
              </div>
              <div>
                <label className="text-[11px] text-gray-text block mb-1">{t('hrv')} (ms)</label>
                <input type="number" value={hrv} onChange={e => setHrv(e.target.value)}
                  placeholder="42" min={1} max={200}
                  className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid" />
              </div>
              <div>
                <label className="text-[11px] text-gray-text block mb-1">{t('steps')}</label>
                <input type="number" value={steps} onChange={e => setSteps(e.target.value)}
                  placeholder="8000" min={0}
                  className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid" />
              </div>
              <div>
                <label className="text-[11px] text-gray-text block mb-1">{t('activeMin')} (min)</label>
                <input type="number" value={activeMin} onChange={e => setActiveMin(e.target.value)}
                  placeholder="30" min={0}
                  className="w-full px-3 py-2.5 rounded-xl border border-steel bg-ice text-[14px] text-[#1a2730] outline-none focus:border-blue-mid" />
              </div>
            </div>
          </div>
        )}

        {/* submit */}
        <button
          onClick={() => submitMutation.mutate()}
          disabled={submitMutation.isPending || done}
          className={`w-full py-3.5 rounded-2xl text-[14px] font-medium transition-colors
            ${done
              ? 'bg-[#4CAF70] text-white'
              : 'bg-blue-deep text-white active:bg-blue-mid disabled:opacity-60'
            }`}
        >
          {done
            ? '✓ ' + t('healthSaved')
            : submitMutation.isPending
            ? t('saving')
            : t('logAndRegenerate')
          }
        </button>

      </div>
    </div>
  )
}
