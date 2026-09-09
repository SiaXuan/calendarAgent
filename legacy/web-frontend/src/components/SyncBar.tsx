import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface Props {
  blockCount: number
  visible: boolean
  onClose: () => void
}

export default function SyncBar({ blockCount, visible, onClose }: Props) {
  const { t } = useTranslation()
  const [syncing, setSyncing] = useState(false)
  const [toast, setToast] = useState(false)

  const doSync = () => {
    setSyncing(true)
    setTimeout(() => {
      onClose()
      setSyncing(false)
      setToast(true)
      setTimeout(() => setToast(false), 2200)
    }, 1400)
  }

  return (
    <>
      {/* sync bar */}
      <div
        className="absolute bottom-16 left-3 right-3 rounded-2xl px-4 py-3 flex items-center justify-between z-10 transition-transform duration-300"
        style={{
          background: '#2A6090',
          transform: visible ? 'translateY(0)' : 'translateY(150%)',
        }}
      >
        <div className="flex flex-col gap-0.5">
          <div className="text-[13px] font-medium text-white">
            {t('blocksReady', { count: blockCount })}
          </div>
          <div className="text-[11px] text-white/60">
            {syncing ? t('writingToCalendar') : t('notYetInCalendar')}
          </div>
        </div>
        <button
          onClick={doSync}
          disabled={syncing}
          className="bg-white text-blue-deep rounded-lg px-3.5 py-1.5 text-[12px] font-medium flex-shrink-0 disabled:opacity-60"
        >
          {syncing ? '…' : t('syncToCalendar')}
        </button>
      </div>

      {/* toast */}
      <div
        className="absolute left-1/2 z-20 rounded-full px-4 py-2 text-[12px] font-medium text-white whitespace-nowrap transition-all duration-250 pointer-events-none"
        style={{
          bottom: 76,
          background: 'rgba(26,74,114,0.92)',
          transform: `translateX(-50%) translateY(${toast ? 0 : 20}px)`,
          opacity: toast ? 1 : 0,
        }}
      >
        {t('addedToCalendar')}
      </div>
    </>
  )
}
