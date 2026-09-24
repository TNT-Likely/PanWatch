import { useSyncExternalStore } from 'react'
import { fetchAPI, isAuthenticated } from '@panwatch/api'

/**
 * 涨跌颜色口径（红涨绿跌 ↔ 绿涨红跌）全局状态。
 * - 生效机制：html[data-stock-mode] 交换 --stock-up/--stock-down 变量值，全部 Tailwind stock-* 类与 CSS 变量读取点自动跟随
 * - 存储：localStorage 实时生效（启动时由 index.html 内联脚本提前应用，防 FOUC）+ PUT /api/settings/price_color_mode 跨设备同步
 * - 值域文档化预留 'market'（按市场混合），当前不做
 */
export type StockColorMode = 'up-red' | 'up-green'

const STORAGE_KEY = 'panwatch-stock-mode'

let current: StockColorMode = 'up-red'
const listeners = new Set<() => void>()

function parseMode(v: string | null): StockColorMode | null {
  return v === 'up-red' || v === 'up-green' ? v : null
}

function apply(m: StockColorMode) {
  if (m === 'up-green') document.documentElement.dataset.stockMode = 'up-green'
  else delete document.documentElement.dataset.stockMode
}

// 模块加载即同步 localStorage 已有口径到状态与 DOM（index.html 内联脚本只设 DOM，
// React 状态必须在此处对齐，否则 Select 等受控组件与实际口径不一致）
const initialMode = parseMode(typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null) ?? 'up-red'
current = initialMode
apply(initialMode)

export function setStockColorMode(m: StockColorMode) {
  current = m
  apply(m)
  try {
    localStorage.setItem(STORAGE_KEY, m)
  } catch {
    /* ignore */
  }
  listeners.forEach(l => l())
  // 后端同步失败不阻塞本地生效
  fetchAPI('/settings/price_color_mode', { method: 'PUT', body: JSON.stringify({ value: m }) })
    .catch((e: unknown) => console.warn('[stock-mode] 后端同步失败，本地值已生效', e))
}

export function useStockColorMode(): StockColorMode {
  return useSyncExternalStore(
    cb => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => current,
  )
}

/**
 * 启动回落：本地无显式设置时从后端读取（多设备一致）。
 * 未登录不发起——401 会触发 fetchAPI 的自动登出副作用，绝不能在冷启动误踢会话。
 */
export async function initStockModeFromBackend(): Promise<void> {
  if (typeof localStorage === 'undefined') return
  if (parseMode(localStorage.getItem(STORAGE_KEY))) return // 本地值优先
  if (!isAuthenticated()) return
  try {
    const settings = await fetchAPI<Array<{ key: string; value: string }>>('/settings')
    const v = parseMode(settings?.find(s => s.key === 'price_color_mode')?.value ?? null)
    if (v && v !== current) {
      current = v
      apply(v)
      listeners.forEach(l => l())
    }
  } catch (e) {
    console.warn('[stock-mode] 后端回落失败，使用默认口径', e)
  }
}
