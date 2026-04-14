import { useTranslation } from 'react-i18next'
import EnergyChart from './EnergyChart'

interface Props {
  healthSummary: string
  energyCurve: number[]
  onLogSleep?: () => void
}

export default function HealthCard({ healthSummary, energyCurve, onLogSleep }: Props) {
  const { t } = useTranslation()

  const hasRealData = !healthSummary.startsWith('No health data')

  const peakHours = energyCurve.map((v,i) => v > 0.72 ? i : null).filter(Boolean) as number[]
  const goodHours = energyCurve.map((v,i) => v > 0.48 && v <= 0.72 ? i : null).filter(Boolean) as number[]
  // Wind-down: first evening hour (≥18:00) where energy drops below 0.45
  const windDownHour = energyCurve
    .map((v, i) => ({ v, i }))
    .filter(({ i }) => i >= 18)
    .find(({ v }) => v < 0.45)?.i ?? 20
  const fmt = (h: number) => h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h-12}pm`
  const range = (hrs: number[]) => hrs.length ? `${fmt(hrs[0])}–${fmt(hrs[hrs.length-1])}` : '–'

  return (
    <div className="bg-white border border-gray-border rounded-2xl p-3.5">
      {/* header row */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[11px] text-gray-text uppercase tracking-wide">{t('healthSnapshot')}</div>
        <button
          onClick={onLogSleep}
          className="flex items-center gap-1 text-[11px] text-blue-mid hover:text-blue-deep"
        >
          {hasRealData ? (
            <>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
                <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
              {t('editSleep')}
            </>
          ) : (
            <>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 5v14M5 12h14"/>
              </svg>
              {t('logSleep')}
            </>
          )}
        </button>
      </div>

      {/* summary text or no-data prompt */}
      {hasRealData ? (
        <div className="text-[13px] text-gray-text mb-0.5">{healthSummary}</div>
      ) : (
        <button
          onClick={onLogSleep}
          className="w-full mb-2 py-2 px-3 rounded-xl border border-dashed border-steel bg-ice text-left"
        >
          <div className="text-[12px] font-medium text-blue-mid">{t('noSleepData')}</div>
          <div className="text-[11px] text-gray-text mt-0.5">{t('noSleepDataSub')}</div>
        </button>
      )}

      <EnergyChart curve={energyCurve} />

      <div className="flex gap-1.5 mt-2.5">
        <div className="flex-1 bg-ice rounded-xl p-2 text-center border border-ice2">
          <div className="text-[15px] font-medium text-blue-deep">{t('peakEnergy')}</div>
          <div className="text-[10px] text-gray-text mt-0.5">{range(peakHours)}</div>
        </div>
        <div className="flex-1 bg-ice rounded-xl p-2 text-center border border-ice2">
          <div className="text-[15px] font-medium text-blue-mid">{t('goodEnergy')}</div>
          <div className="text-[10px] text-gray-text mt-0.5">{range(goodHours)}</div>
        </div>
        <div className="flex-1 bg-ice rounded-xl p-2 text-center border border-ice2">
          <div className="text-[15px] font-medium text-gray-text">{t('windDown')}</div>
          <div className="text-[10px] text-gray-text mt-0.5">{t('windDownAfter', { time: fmt(windDownHour) })}</div>
        </div>
      </div>
    </div>
  )
}
