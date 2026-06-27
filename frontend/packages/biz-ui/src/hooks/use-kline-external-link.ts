import { useCallback, useEffect, useState } from 'react'
import {
  APP_SETTINGS_CHANGED_EVENT,
  isKlineExternalLinkEnabled,
} from '@panwatch/api/settings'

export function useKlineExternalLinkEnabled(): boolean {
  const [enabled, setEnabled] = useState(false)

  const load = useCallback(async () => {
    try {
      setEnabled(await isKlineExternalLinkEnabled())
    } catch {
      setEnabled(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const onChanged = () => { void load() }
    window.addEventListener(APP_SETTINGS_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(APP_SETTINGS_CHANGED_EVENT, onChanged)
  }, [load])

  return enabled
}
