export type SuggestionAction =
  | 'buy'
  | 'add'
  | 'reduce'
  | 'sell'
  | 'hold'
  | 'watch'
  | 'alert'
  | 'avoid'

export const suggestionActionColors: Record<SuggestionAction, string> = {
  // 动作徽章：暗色改「暗底亮字」——亮底白字在暗底上对比 1.67-3.06 不达 AA（审计）
  buy: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
  add: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
  reduce: 'bg-stock-down text-white dark:bg-stock-down/15 dark:text-stock-down',
  sell: 'bg-stock-down text-white dark:bg-stock-down/15 dark:text-stock-down',
  hold: 'bg-amber-500 text-white dark:bg-amber-400/15 dark:text-amber-300',
  watch: 'bg-slate-500 text-white dark:bg-slate-400/15 dark:text-slate-300',
  alert: 'bg-blue-500 text-white dark:bg-blue-400/15 dark:text-blue-300',
  avoid: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
}

export const suggestionActionLabels: Record<SuggestionAction, string> = {
  buy: '买入',
  add: '加仓',
  reduce: '减仓',
  sell: '卖出',
  hold: '持有',
  watch: '观望',
  avoid: '回避',
  alert: '提醒',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'
  if (raw === 'alert') return 'alert'
  if (/买入|买|建仓/.test(raw)) return 'buy'
  if (/加仓|增持|补仓/.test(raw)) return 'add'
  if (/减仓|减持/.test(raw)) return 'reduce'
  if (/清仓|卖出|止损|卖/.test(raw)) return 'sell'
  if (/持有|持仓/.test(raw)) return 'hold'
  if (/观望|中性|等待/.test(raw)) return 'watch'
  if (/回避|规避|避免/.test(raw)) return 'avoid'
  return null
}

export function resolveSuggestionAction(action?: string, label?: string): SuggestionAction {
  return normalizeSuggestionAction(action, label) || 'watch'
}

export function resolveSuggestionLabel(action?: string, label?: string, fallback = '观望'): string {
  const normalized = normalizeSuggestionAction(action, label)
  if (normalized) return suggestionActionLabels[normalized] || fallback
  return String(label || '').trim() || fallback
}

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}
