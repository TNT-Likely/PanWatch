import { useState, useEffect } from 'react'

/** 用户选择的主题模式(system = 跟随系统)。 */
export type ThemeMode = 'light' | 'dark' | 'system'
/** 实际生效的主题(system 解析后的结果)。 */
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'aiwatch-theme'
// 旧版品牌键：仅用于读取回落，读到后回写新键（write-through），保留不清除
const LEGACY_KEY = 'panwatch-theme'

function readMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_KEY)
  if (stored !== null && localStorage.getItem(STORAGE_KEY) === null) {
    try {
      localStorage.setItem(STORAGE_KEY, stored)
    } catch {
      /* ignore */
    }
  }
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readMode)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  // 跟随系统:监听 OS 主题变化,实时反映
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // 生效主题:显式 light/dark 直接用,system 则跟随当前系统
  const theme: Theme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(theme)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [theme, mode])

  // 兼容旧调用:在亮/暗间切换(会把模式落为显式 light/dark)
  const toggleTheme = () => setMode(theme === 'dark' ? 'light' : 'dark')

  return { theme, mode, setMode, toggleTheme }
}
