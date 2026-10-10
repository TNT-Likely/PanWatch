import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { DashboardOverviewInstrument } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import Sparkline from '@/components/Sparkline'
import { marketSignTextClass } from '@/lib/market-colors'

export const OVERVIEW_GROUPS = ['china', 'hongKong', 'us', 'canada', 'europe', 'asia', 'commodities', 'crypto', 'fixedIncome']
const STORAGE_KEY = 'panwatch.globalOverview.v1'
type Preferences = { order: string[]; hidden: string[] }
const defaults = (): Preferences => ({ order: [], hidden: [] })
export function loadOverviewPreferences(): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    const strings = (v: unknown) => Array.isArray(v) ? [...new Set(v.filter((x): x is string => typeof x === 'string'))] : []
    return { order: strings(value?.order), hidden: strings(value?.hidden) }
  } catch { return defaults() }
}
export function orderedItems<T>(items: T[], order: string[], key: (item: T) => string): T[] {
  const rank = (item: T) => { const n = order.indexOf(key(item)); return n < 0 ? order.length : n }
  return [...items].sort((a, b) => rank(a) - rank(b))
}

export default function GlobalMarketOverview({ instruments, loading = false }: { instruments: DashboardOverviewInstrument[]; loading?: boolean }) {
  const { t, i18n } = useTranslation('configuration')
  const translate = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => translate(`globalOverview.${key}`, options)
  const [editing, setEditing] = useState(false)
  const [prefs, setPrefs] = useState(loadOverviewPreferences)
  const groups = useMemo(() => orderedItems(OVERVIEW_GROUPS, prefs.order, id => `group:${id}`), [prefs.order])
  const save = (next: Preferences) => {
    setPrefs(next)
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch { /* Storage can be disabled. Keep this session usable. */ }
  }
  const toggle = (key: string) => save({ ...prefs, hidden: prefs.hidden.includes(key) ? prefs.hidden.filter(id => id !== key) : [...prefs.hidden, key] })
  const move = (keys: string[], index: number, offset: number) => {
    const ordered = [...keys]
    const next = index + offset
    if (next < 0 || next >= ordered.length) return
    ;[ordered[index], ordered[next]] = [ordered[next], ordered[index]]
    save({ ...prefs, order: [...prefs.order.filter(key => !keys.includes(key)), ...ordered] })
  }
  const controls = (key: string, name: string, keys: string[], index: number) => (
    <div className="flex items-center gap-1">
      <label className="flex items-center gap-1 text-xs">
        <input type="checkbox" checked={!prefs.hidden.includes(key)} onChange={() => toggle(key)} aria-label={tr('show', { name })} />
        {name}
      </label>
      <Button size="sm" variant="ghost" disabled={index === 0} aria-label={tr('up', { name })} onClick={() => move(keys, index, -1)}>↑</Button>
      <Button size="sm" variant="ghost" disabled={index === keys.length - 1} aria-label={tr('down', { name })} onClick={() => move(keys, index, 1)}>↓</Button>
    </div>
  )
  const asOf = (ix: DashboardOverviewInstrument) => {
    if (!ix.as_of) return tr('unknownTime')
    if (/^\d{4}-\d{2}-\d{2}$/.test(ix.as_of)) return tr('dateOnly', { date: ix.as_of })
    const date = new Date(ix.as_of)
    if (!Number.isFinite(date.getTime())) return tr('unknownTime')
    return tr('asOf', { time: new Intl.DateTimeFormat(i18n.language, { timeZone: ix.timezone, dateStyle: 'medium', timeStyle: 'short' }).format(date) })
  }
  const visibleCount = instruments.filter(ix => !prefs.hidden.includes(`group:${ix.group}`) && !prefs.hidden.includes(ix.id)).length
  return (
    <section className="card mb-3 p-4" aria-label={tr('title')}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{tr('title')}</h2>
        <Button size="sm" variant="ghost" onClick={() => setEditing(!editing)} aria-expanded={editing}>{tr(editing ? 'done' : 'configure')}</Button>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">{tr('hint')}</p>
      {editing && <div className="mb-3 rounded border p-2">
        <div className="flex items-center justify-between gap-2"><p className="text-xs text-muted-foreground">{tr('preferencesHint')}</p><Button size="sm" variant="outline" onClick={() => save(defaults())}>{tr('reset')}</Button></div>
        {groups.map((group, index) => <div key={group} className="my-2">
          {controls(`group:${group}`, tr(`groups.${group}`), groups.map(id => `group:${id}`), index)}
          <div className="grid gap-1 pl-3 sm:grid-cols-2 lg:grid-cols-3">
            {orderedItems(instruments.filter(ix => ix.group === group), prefs.order, ix => ix.id).map((ix, i, items) => <div key={ix.id}>{controls(ix.id, tr(`names.${ix.name_key}`), items.map(item => item.id), i)}</div>)}
          </div>
        </div>)}
      </div>}
      {!instruments.length && <p className="text-xs text-muted-foreground" role="status">{tr(loading ? 'loading' : 'noData')}</p>}
      {!!instruments.length && visibleCount === 0 && <p className="text-xs text-muted-foreground">{tr('hidden')}</p>}
      {groups.filter(group => !prefs.hidden.includes(`group:${group}`)).map(group => {
        const items = orderedItems(instruments.filter(ix => ix.group === group && !prefs.hidden.includes(ix.id)), prefs.order, ix => ix.id)
        if (!items.length) return null
        return <div key={group} className="mb-4 last:mb-0">
          <h3 className="mb-2 text-xs font-semibold">{tr(`groups.${group}`)}</h3>
          <div className="grid grid-cols-1 gap-2 min-[390px]:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
            {items.map(ix => {
              const yieldQuote = ix.instrument_type === 'yield'
              const delta = yieldQuote ? ix.change_bps : ix.change_pct
              // A nonpositive futures baseline has no displayed percentage;
              // its explicit price change still gives a reliable direction.
              const direction = ix.instrument_type === 'futures' ? ix.change_amount : delta
              const value = ix.current_price
              return <article key={ix.id} className="card-subtle min-w-0 p-2.5" aria-label={tr(`names.${ix.name_key}`)}>
                <div className="text-xs font-medium" title={`${ix.provider_symbol} · ${ix.provider_name || ix.provider_symbol}`}>{tr(`names.${ix.name_key}`)}</div>
                <div className="flex flex-wrap items-baseline justify-between gap-1">
                  <span className="font-mono text-base">{value != null && Number.isFinite(value) ? `${value.toFixed(yieldQuote ? 3 : 2)}${yieldQuote ? '%' : ''}` : '--'}</span>
                  <span className={`font-mono text-xs ${marketSignTextClass(direction)}`}>{delta != null && Number.isFinite(delta) ? `${delta > 0 ? '+' : ''}${delta.toFixed(2)} ${yieldQuote ? tr('bps') : '%'}` : '--'}</span>
                </div>
                <div className="text-[10px] text-muted-foreground">{tr(`kinds.${ix.instrument_type}`)}{!yieldQuote && ix.instrument_type !== 'index' ? ` · ${tr(`units.${ix.id}`)}` : ''}{ix.currency ? ` · ${ix.currency}` : ''}</div>
                {ix.spark && ix.spark.length >= 2 ? <div className="my-1.5"><Sparkline data={ix.spark} height={26} className={marketSignTextClass(direction)} /></div> : <div className="my-1.5 text-[10px] text-muted-foreground">{tr('chartUnavailable')}</div>}
                <div className="text-[10px] text-muted-foreground">{tr(`statuses.${ix.status}`)} · {ix.provider}</div>
                <div className="break-words text-[10px] text-muted-foreground">{asOf(ix)} · {ix.timezone}</div>
              </article>
            })}
          </div>
        </div>
      })}
    </section>
  )
}
