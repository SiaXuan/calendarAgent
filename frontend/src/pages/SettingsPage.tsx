import { useTranslation } from 'react-i18next'
import { useUserPreferences } from '../context/UserPreferencesContext'

const LANGUAGES = [
  { code: 'zh-CN', label: '中文（简体）' },
  { code: 'en',    label: 'English' },
] as const

export default function SettingsPage() {
  const { t } = useTranslation()
  const { prefs, updatePrefs } = useUserPreferences()

  if (!prefs) {
    return (
      <div className="flex-1 flex items-center justify-center bg-ice">
        <span className="text-[13px] text-gray-text">{t('loading')}</span>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto bg-ice">
      {/* topbar */}
      <div className="h-13 bg-[#F7FAFE] border-b border-ice2 flex items-center px-5 flex-shrink-0">
        <span className="text-[17px] font-medium text-[#1a2730]">{t('settings')}</span>
      </div>

      <div className="p-3 flex flex-col gap-3">
        {/* Language */}
        <div className="bg-white border border-gray-border rounded-2xl overflow-hidden">
          <div className="px-4 py-3 border-b border-ice2">
            <span className="text-[12px] text-gray-text uppercase tracking-wide">{t('language')}</span>
          </div>
          {LANGUAGES.map(({ code, label }) => (
            <button
              key={code}
              onClick={() => updatePrefs({ language: code as 'en' | 'zh-CN' })}
              className="w-full px-4 py-3.5 flex items-center justify-between border-b border-ice2 last:border-0 active:bg-ice"
            >
              <span className="text-[14px] text-[#1a2730]">{label}</span>
              {prefs.language === code && (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                  stroke="#4E8BB5" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              )}
            </button>
          ))}
        </div>

        {/* Work hours */}
        <div className="bg-white border border-gray-border rounded-2xl overflow-hidden">
          <div className="px-4 py-3 border-b border-ice2">
            <span className="text-[12px] text-gray-text uppercase tracking-wide">{t('workHours')}</span>
          </div>
          <div className="px-4 py-3.5 flex items-center justify-between border-b border-ice2">
            <span className="text-[14px] text-[#1a2730]">{t('workStart')}</span>
            <select
              value={prefs.work_start}
              onChange={e => updatePrefs({ work_start: Number(e.target.value) })}
              className="text-[13px] text-blue-deep bg-transparent border-none outline-none"
            >
              {Array.from({ length: 13 }, (_, i) => i + 5).map(h => (
                <option key={h} value={h}>{h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h-12}pm`}</option>
              ))}
            </select>
          </div>
          <div className="px-4 py-3.5 flex items-center justify-between">
            <span className="text-[14px] text-[#1a2730]">{t('workEnd')}</span>
            <select
              value={prefs.work_end}
              onChange={e => updatePrefs({ work_end: Number(e.target.value) })}
              className="text-[13px] text-blue-deep bg-transparent border-none outline-none"
            >
              {Array.from({ length: 9 }, (_, i) => i + 16).map(h => (
                <option key={h} value={h}>{h === 24 ? '12am' : h === 12 ? '12pm' : `${h-12}pm`}</option>
              ))}
            </select>
          </div>
        </div>
      </div>
    </div>
  )
}
