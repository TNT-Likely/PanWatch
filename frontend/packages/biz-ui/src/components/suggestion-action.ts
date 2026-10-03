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
  buy: 'bg-market-up text-white',
  add: 'bg-market-up/85 text-white',
  reduce: 'bg-market-down/85 text-white',
  sell: 'bg-market-down text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  alert: 'bg-blue-500 text-white',
  avoid: 'bg-red-600 text-white',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy/add' || raw === 'add/buy') return /加仓|增持|补仓/.test(label || '') ? 'add' : 'buy'
  if (raw === 'sell/reduce' || raw === 'reduce/sell') return /减仓|减持/.test(label || '') ? 'reduce' : 'sell'
  if (raw === 'buy' && /增持|overweight/i.test(label || '')) return 'add'
  if (raw === 'sell' && /减持|underweight/i.test(label || '')) return 'reduce'
  if (raw === 'buy' || raw === 'build') return 'buy'
  if (raw === 'add' || raw === 'increase' || raw === 'overweight') return 'add'
  if (raw === 'reduce' || raw === 'decrease' || raw === 'underweight') return 'reduce'
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

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}

export interface SuggestionStateInput {
  action?: string
  action_label?: string
  rating_raw?: string
  status?: string
  review_required?: boolean
  attention_required?: boolean
  meta?: Record<string, any>
}

export function suggestionPresentation(suggestion: SuggestionStateInput) {
  const state = { ...suggestion.meta?.suggestion_state, ...Object.fromEntries(Object.entries(suggestion).filter(([, value]) => value !== undefined)) } as SuggestionStateInput
  const review = state.review_required === true || state.status === 'review' ||
    state.action === 'review' || state.rating_raw === 'review' || /待.*复核|review required/i.test(state.action_label || '')
  const action = review ? 'watch' : resolveSuggestionAction(state.rating_raw || state.action, state.action_label)
  const attention = state.attention_required === true || action === 'alert'
  return {
    action: action === 'alert' ? 'watch' as const : action,
    review,
    attention,
    labelKey: review ? 'suggestionBadge.review' : `kline.actions.${attention && (action === 'alert' || action === 'watch') ? 'alert' : action}`,
    colorClass: review ? 'bg-orange-500 text-white' : suggestionActionColors[attention && action === 'watch' ? 'alert' : action],
  }
}
