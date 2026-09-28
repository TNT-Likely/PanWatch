import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { normalizeLocale } from '@/i18n'
import {
  applyMarketColorScheme,
  getMarketColorPalette,
  MARKET_COLOR_STORAGE_KEY,
  normalizeMarketColorPreference,
  readMarketColorPreference,
  resolveMarketColorScheme,
  type EffectiveMarketColorScheme,
  type MarketColorPalette,
  type MarketColorPreference,
} from '@/lib/market-colors'

interface MarketColorContextValue {
  preference: MarketColorPreference
  effectiveScheme: EffectiveMarketColorScheme
  palette: MarketColorPalette
  setPreference: (preference: MarketColorPreference) => void
}

const MarketColorContext = createContext<MarketColorContextValue | null>(null)

export function MarketColorProvider({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const locale = normalizeLocale(i18n.resolvedLanguage || i18n.language)
  const [preference, setPreferenceState] = useState<MarketColorPreference>(readMarketColorPreference)
  const effectiveScheme = resolveMarketColorScheme(preference, locale)
  const palette = useMemo(() => getMarketColorPalette(effectiveScheme), [effectiveScheme])

  useLayoutEffect(() => {
    applyMarketColorScheme(preference, locale)
  }, [locale, preference])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== MARKET_COLOR_STORAGE_KEY) return
      setPreferenceState(normalizeMarketColorPreference(event.newValue))
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const setPreference = useCallback((next: MarketColorPreference) => {
    const normalized = normalizeMarketColorPreference(next)
    window.localStorage.setItem(MARKET_COLOR_STORAGE_KEY, normalized)
    setPreferenceState(normalized)
  }, [])

  const value = useMemo<MarketColorContextValue>(() => ({
    preference,
    effectiveScheme,
    palette,
    setPreference,
  }), [effectiveScheme, palette, preference, setPreference])

  return <MarketColorContext.Provider value={value}>{children}</MarketColorContext.Provider>
}

export function useMarketColors(): MarketColorContextValue {
  const value = useContext(MarketColorContext)
  if (!value) throw new Error('useMarketColors must be used inside MarketColorProvider')
  return value
}
