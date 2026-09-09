import React from 'react'
import { useTranslation } from 'react-i18next'
import OrbButton from './OrbButton'

type Tab = 'today' | 'tasks' | 'chat' | 'settings'

interface Props {
  active: Tab
  onChange: (t: Tab) => void
}

const NAV_STROKE = '#8A939E'
const NAV_ACTIVE = '#2A6090'

export default function BottomNav({ active, onChange }: Props) {
  const { t } = useTranslation()

  const item = (tab: Tab, icon: React.ReactNode) => (
    <button
      key={tab}
      onClick={() => onChange(tab)}
      className="flex-1 flex flex-col items-center justify-center gap-0.5 py-1"
    >
      <div style={{ stroke: active === tab ? NAV_ACTIVE : NAV_STROKE }}>{icon}</div>
      <span className="text-[10px]" style={{ color: active === tab ? NAV_ACTIVE : NAV_STROKE }}>
        {t(tab)}
      </span>
    </button>
  )

  return (
    <div className="h-[60px] bg-[#F7FAFE] border-t border-ice2 flex items-center">
      {item('today',
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>
        </svg>
      )}
      {item('tasks',
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>
        </svg>
      )}
      <div className="flex-1 flex justify-center">
        <OrbButton />
      </div>
      {item('chat',
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
        </svg>
      )}
      {item('settings',
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>
        </svg>
      )}
    </div>
  )
}
