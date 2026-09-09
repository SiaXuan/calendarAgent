import React, { createContext, useContext, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { UserPreferences } from '../api/types'
import { fetchPreferences, patchPreferences } from '../api/preferences'

interface PrefsCtx {
  prefs: UserPreferences | null
  updatePrefs: (p: Partial<UserPreferences>) => Promise<void>
}

const Ctx = createContext<PrefsCtx>({ prefs: null, updatePrefs: async () => {} })

export function UserPreferencesProvider({ children }: { children: React.ReactNode }) {
  const [prefs, setPrefs] = useState<UserPreferences | null>(null)
  const { i18n } = useTranslation()

  useEffect(() => {
    fetchPreferences().then(p => {
      setPrefs(p)
      i18n.changeLanguage(p.language)
    }).catch(() => {})
  }, [])

  const updatePrefs = async (data: Partial<UserPreferences>) => {
    const updated = await patchPreferences(data)
    setPrefs(updated)
    if (data.language) i18n.changeLanguage(data.language)
  }

  return <Ctx.Provider value={{ prefs, updatePrefs }}>{children}</Ctx.Provider>
}

export const useUserPreferences = () => useContext(Ctx)
