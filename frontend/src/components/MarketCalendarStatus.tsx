import { useEffect, useState } from 'react'
import { CalendarDays, ChevronDown, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { fetchAPI } from '@panwatch/api'
import { Popover, PopoverContent, PopoverTrigger } from '@panwatch/base-ui/components/ui/popover'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { getCurrentLocale } from '@/i18n'

export interface MarketStatus {
  code: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
  local_date?: string
  timezone: string
}

interface CalendarDay {
  date: string
  is_trading_day: boolean | null
  reason: string
  session_times: { open: string; close: string }[]
}
interface TradingCalendars {
  timezone: string
  markets: {
    market: string; status: string; next_open: string | null
    local_date: string; local_time: string; timezone: string; days: CalendarDay[]
  }[]
}

export function calendarTime(instant: string, timezone: string, locale: string) {
  const date = new Date(instant)
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const part = (type: string) => parts.find(value => value.type === type)?.value
  return {
    date: `${part('year')}-${part('month')}-${part('day')}`,
    shortDate: `${part('month')}/${part('day')}`,
    clock: new Intl.DateTimeFormat(locale, { timeZone: timezone, hour: '2-digit',
      minute: '2-digit', hourCycle: 'h23' }).format(date),
  }
}

const statusDot = (status: string) => status === 'trading' ? 'bg-emerald-500'
  : ['pre_market', 'break'].includes(status) ? 'bg-amber-500' : 'bg-slate-400'
const columns = 'grid grid-cols-[64px_repeat(3,minmax(0,1fr))] sm:grid-cols-[80px_repeat(3,minmax(0,1fr))]'

export function MarketCalendarStatus({ markets, labelMarket }: {
  markets: MarketStatus[]; labelMarket: (code: string) => string
}) {
  const { t } = useTranslation('configuration')
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<TradingCalendars | null>(null)
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(false)
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const calendarState = markets.map(market => `${market.code}:${market.local_date}:${market.status}`).join('|')
  const locale = getCurrentLocale()

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setLoading(true)
    setError(false)
    fetchAPI<TradingCalendars>(`/stocks/markets/calendar?days=14&timezone=${encodeURIComponent(timezone)}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [open, calendarState, timezone, retry])

  const weekday = (date: string) => new Intl.DateTimeFormat(locale, {
    weekday: 'short', timeZone: 'UTC',
  }).format(new Date(`${date}T12:00:00Z`))
  const sessionLabel = (instant: string, day: string) => {
    const value = calendarTime(instant, timezone, locale)
    const offset = Math.round((Date.parse(value.date) - Date.parse(day)) / 86400000)
    if (offset === 0) return value.clock
    if (offset === 1) return t('stocksPage.calendar.nextDay', { time: value.clock })
    if (offset === -1) return t('stocksPage.calendar.previousDay', { time: value.clock })
    return `${value.shortDate} ${value.clock}`
  }
  const timezoneLabel = timezone === 'Asia/Shanghai' ? t('stocksPage.calendar.beijing') : timezone

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="group shrink-0 flex items-center gap-2 rounded-md px-1 py-1 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-label={t('stocksPage.calendar.open')}>
          {markets.map(market => <span key={market.code} className="flex items-center gap-1 md:gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${statusDot(market.status)}`} />
            <span className="text-[11px] text-muted-foreground">{labelMarket(market.code)}</span>
            <span className={`text-[10px] ${market.is_trading ? 'text-emerald-600' : 'text-muted-foreground/60'} hidden sm:inline`}>{t(`stocksPage.marketStatus.${market.status}`, { defaultValue: market.status_text })}</span>
          </span>)}
          <ChevronDown className="h-3 w-3 text-muted-foreground/50 group-hover:text-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" sideOffset={8} collisionPadding={12} className="flex max-h-[calc(100dvh-24px)] w-[min(640px,calc(100vw-24px))] flex-col overflow-hidden rounded-2xl p-0 shadow-xl" aria-label={t('stocksPage.calendar.title')}>
        <div className="flex shrink-0 items-center gap-3 px-4 py-4 sm:px-5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary"><CalendarDays className="h-4 w-4" /></div>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-sm">{t('stocksPage.calendar.title')}</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">{t('stocksPage.calendar.displayTimezone', { timezone: timezoneLabel })}</p>
          </div>
          <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 text-muted-foreground" aria-label={t('stocksPage.calendar.close')} onClick={() => setOpen(false)}><X className="h-4 w-4" /></Button>
        </div>
        {loading ? <p className="p-5 text-xs text-muted-foreground" role="status">{t('stocksPage.calendar.loading')}</p>
          : error ? <div className="p-5 text-xs"><p>{t('stocksPage.calendar.error')}</p><Button size="sm" variant="outline" className="mt-2" onClick={() => setRetry(value => value + 1)}>{t('stocksPage.calendar.retry')}</Button></div>
          : data && <>
            <div className="grid shrink-0 grid-cols-3 gap-2 px-3 pb-4 sm:gap-3 sm:px-5">
              {data.markets.map(market => {
                const next = market.next_open ? calendarTime(market.next_open, timezone, locale) : null
                const live = markets.find(value => value.code === market.market)
                const status = live?.status ?? market.status
                const localDate = live?.local_date ?? market.local_date
                const localTime = live?.local_time ?? market.local_time
                return <div key={market.market} className="min-w-0 rounded-xl border border-border/70 bg-muted/20 p-2.5 sm:p-3">
                  <div className="flex flex-wrap items-center justify-between gap-1">
                    <span className="text-xs font-medium">{labelMarket(market.market)}</span>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"><span className={`h-1 w-1 rounded-full ${statusDot(status)}`} />{t(`stocksPage.marketStatus.${status}`, { defaultValue: status })}</span>
                  </div>
                  <p className="mt-1 text-[10px] tabular-nums text-muted-foreground" title={live?.timezone ?? market.timezone}>{t('stocksPage.calendar.localDate', { date: localDate?.slice(5).replace('-', '/'), time: localTime })}</p>
                  <p className="mt-3 text-[10px] text-muted-foreground">{t('stocksPage.calendar.nextOpen')}</p>
                  <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 text-sm font-semibold tabular-nums tracking-tight">
                    {next ? <><span>{next.shortDate}</span><span>{next.clock}</span></> : <span className="text-xs font-normal text-muted-foreground">{t('stocksPage.calendar.pending')}</span>}
                  </p>
                </div>
              })}
            </div>
            <div className="flex min-h-0 flex-col border-t border-border/60" role="table" aria-label={t('stocksPage.calendar.title')}>
              <div className={`${columns} shrink-0 items-center border-b border-border/60 bg-muted/30 px-3 py-2.5 text-[11px] font-medium text-muted-foreground sm:px-5`} role="row">
                <span role="columnheader">{t('stocksPage.calendar.date')}</span>
                {data.markets.map(market => <span className="text-center" key={market.market} role="columnheader">{labelMarket(market.market)}</span>)}
              </div>
              <div className="min-h-0 max-h-[340px] overflow-y-auto overscroll-contain scrollbar" role="rowgroup">
                {data.markets[0]?.days.map((day, index) => <div key={day.date} role="row" className={`${columns} items-center border-b border-border/40 px-3 py-3 last:border-b-0 hover:bg-muted/20 sm:px-5`}>
                  <div role="cell">
                    <p className="text-xs font-medium tabular-nums">{day.date.slice(5).replace('-', '/')}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">{weekday(day.date)}</p>
                  </div>
                  {data.markets.map(market => {
                    const current = market.days[index]
                    return <div key={market.market} role="cell" className="text-center">
                      <p className={`text-[11px] ${current.is_trading_day ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground/70'}`}>{t(`stocksPage.calendar.reasons.${current.reason}`, { defaultValue: current.reason })}</p>
                      {current.session_times.map(session => <p key={session.open} className="mt-1 text-[10px] leading-relaxed text-muted-foreground tabular-nums whitespace-nowrap">{sessionLabel(session.open, day.date)} – {sessionLabel(session.close, day.date)}</p>)}
                    </div>
                  })}
                </div>)}
              </div>
            </div>
          </>}
        <div className="shrink-0 border-t border-border/60 bg-muted/10 px-4 py-3 text-[10px] leading-relaxed text-muted-foreground sm:px-5">
          <p>{t('stocksPage.calendar.statusRule')}</p>
          <p className="mt-0.5 text-muted-foreground/70">{t('stocksPage.calendar.source')}</p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
