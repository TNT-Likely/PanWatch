import { useCallback, useEffect, useMemo, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { fetchAPI } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'
import { KlineIndicators, localizeTechnicalStatus } from '@panwatch/biz-ui/components/kline-indicators'

export interface KlineSummaryData {
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>
  last_close?: number | null
  recent_5_up?: number | null
  trend?: string
  macd_status?: string
  macd_cross?: string | null
  macd_cross_days?: number | null
  macd_hist?: number | null
  rsi6?: number | null
  rsi_status?: string
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  volume_ratio?: number | null
  volume_trend?: string
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_width?: number | null
  boll_status?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  kline_pattern?: string | null
  support?: number | null
  resistance?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
  change_5d?: number | null
  change_20d?: number | null
  amplitude?: number | null
  amplitude_avg5?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface KlineSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  initialSummary?: KlineSummaryData | null
}

type Translate = (key: string, options?: Record<string, unknown>) => string

function formatLocalDateTime(iso: string | undefined, locale: string): string {
  if (!iso) return ''
  try {
    const date = new Date(iso)
    if (Number.isNaN(date.getTime())) return ''
    return date.toLocaleString(locale, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

function buildLocalizedSuggestion(s: KlineSummaryData, holding: boolean | undefined, tr: Translate) {
  const scored = buildKlineSuggestion(s, holding, tr)
  const items: Array<{ text: string; delta: number }> = []
  const add = (key: string, delta: number, options?: Record<string, unknown>) => items.push({ text: tr(`items.${key}`, options), delta })

  if (s.trend?.includes('多头')) add('trendBull', 2)
  else if (s.trend?.includes('空头')) add('trendBear', -2)
  if (s.macd_status?.includes('金叉')) add('macdGolden', 2)
  if (s.macd_status?.includes('死叉')) add('macdDeath', -2)
  if (typeof s.macd_hist === 'number') {
    const statusKey = s.macd_hist > 0 ? 'positive' : s.macd_hist < 0 ? 'negative' : 'neutral'
    add('macdHist', s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0, { status: tr(`statuses.${statusKey}`) })
  }
  if (s.rsi_status?.includes('超卖')) add('rsiOversold', 1)
  else if (s.rsi_status?.includes('偏强')) add('rsiStrong', 1)
  else if (s.rsi_status?.includes('超买')) add('rsiOverbought', -1)
  else if (s.rsi_status?.includes('偏弱')) add('rsiWeak', -1)
  if (s.kdj_status?.includes('金叉')) add('kdjGolden', 1)
  if (s.kdj_status?.includes('死叉')) add('kdjDeath', -1)
  if (s.boll_status?.includes('突破上轨')) add('bollUpper', 1)
  else if (s.boll_status?.includes('跌破下轨')) add('bollLower', -1)
  if (s.volume_trend?.includes('放量')) add('volumeUp', 1)
  else if (s.volume_trend?.includes('缩量')) add('volumeDown', -1)
  if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('nearSupport', 1)
  if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('nearResistance', -1)

  return {
    ...scored,
    action_label: tr(`actions.${scored.action}`),
    signal: items.filter((item) => item.delta !== 0).slice(0, 4).map((item) => item.text).join(' / '),
    items,
  }
}

export function KlineSummaryDialog({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  hasPosition,
  initialSummary = null,
}: KlineSummaryDialogProps) {
  const { t, i18n } = useTranslation('bizUi')
  const tr: Translate = (key, options) =>
    (t as unknown as Translate)(`kline.${key}`, options)
  const locale = (i18n.resolvedLanguage || i18n.language).toLowerCase().startsWith('en') ? 'en-US' : 'zh-CN'
  const english = locale === 'en-US'
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !symbol) return
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    setSummary(null)
    const resolvedMarket = market || 'CN'
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(resolvedMarket)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = useMemo(
    () => effectiveSummary ? buildLocalizedSuggestion(effectiveSummary, hasPosition, tr) : null,
    [effectiveSummary, hasPosition, i18n.resolvedLanguage, i18n.language],
  )

  const handleAskAI = useCallback(() => {
    if (!effectiveSummary) return
    const s = effectiveSummary
    const indicators = [s.trend, s.macd_status, s.rsi_status, s.kdj_status, s.boll_status, s.volume_trend]
      .filter(Boolean)
      .map((value) => localizeTechnicalStatus(value, tr))
      .join(', ')
    const parts = english
      ? [
          indicators && `Technical indicators: ${indicators}`,
          s.support != null && `Support: ${s.support.toFixed(2)}`,
          s.resistance != null && `Resistance: ${s.resistance.toFixed(2)}`,
          s.last_close != null && `Close: ${s.last_close.toFixed(2)}`,
          suggestion && `Technical score: ${suggestion.action_label} (${suggestion.score})`,
        ]
      : [
          indicators && `技术指标：${indicators}`,
          s.support != null && `支撑位：${s.support.toFixed(2)}`,
          s.resistance != null && `压力位：${s.resistance.toFixed(2)}`,
          s.last_close != null && `收盘价：${s.last_close.toFixed(2)}`,
          suggestion && `技术评分：${suggestion.action_label} (${suggestion.score})`,
        ]
    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
      detail: { symbol, market, stockName: stockName || symbol, pageContext: parts.filter(Boolean).join('\n') },
    }))
    onOpenChange(false)
  }, [effectiveSummary, suggestion, symbol, market, stockName, onOpenChange, english, i18n.resolvedLanguage, i18n.language])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" onClick={(event) => event.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>{tr('title')}</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {tr('period', { value: effectiveSummary?.timeframe || '1d' })}
                  {effectiveSummary?.asof ? ` · ${tr('dataAsOf', { value: effectiveSummary.asof })}` : ''}
                  {effectiveSummary?.computed_at ? ` · ${tr('calculatedAt', { value: formatLocalDateTime(effectiveSummary.computed_at, locale) })}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">{tr('loading')}</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">{tr('empty')}</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="rounded-lg border border-border/30 bg-accent/20 p-3">
                <div className="flex items-center justify-between gap-2">
                  <TechnicalBadge label={suggestion.action_label} tone={technicalToneFromSuggestionAction(suggestion.action, suggestion.action_label)} size="sm" />
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? tr('held') : tr('notHeld')} · {tr('score', { value: suggestion.score })}
                  </span>
                </div>
                {suggestion.signal && <div className="mt-2 text-[12px] font-medium text-foreground">{suggestion.signal}</div>}
                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((item, index) => (
                      <div key={`${item.text}-${index}`} className="flex items-center justify-between gap-3 text-[11px]">
                        <span className="text-muted-foreground">{item.text}</span>
                        <span className={`font-mono ${item.delta > 0 ? 'text-rose-500' : item.delta < 0 ? 'text-emerald-500' : 'text-muted-foreground'}`}>
                          {item.delta > 0 ? '+' : ''}{item.delta}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-2 text-[10px] text-muted-foreground/70">{tr('disclaimer')}</div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">{tr('hoverHint')}</div>
            <KlineIndicators summary={effectiveSummary} />

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex flex-wrap gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && <span>{tr('metrics.fiveDay', { value: `${effectiveSummary.change_5d >= 0 ? '+' : ''}${effectiveSummary.change_5d.toFixed(2)}` })}</span>}
                {effectiveSummary.change_20d != null && <span>{tr('metrics.twentyDay', { value: `${effectiveSummary.change_20d >= 0 ? '+' : ''}${effectiveSummary.change_20d.toFixed(2)}` })}</span>}
                {effectiveSummary.amplitude != null && <span>{tr('metrics.amplitude', { value: effectiveSummary.amplitude.toFixed(2) })}</span>}
              </div>
            )}

            <details className="group">
              <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">
                {tr('rules.title')} <span className="text-[10px]">{tr('rules.expand')}</span>
              </summary>
              <div className="mt-2 space-y-2 whitespace-pre-wrap rounded bg-accent/20 p-2 text-[11px] text-muted-foreground">
                <div className="font-medium text-foreground">{tr('rules.recommendation')}</div>
                <div>{tr('rules.unheld')}</div>
                <div>{tr('rules.held')}</div>
                <div className="font-medium text-foreground">{tr('rules.scoring')}</div>
                {['trend', 'macd', 'rsi', 'kdj', 'boll', 'volume', 'levels'].map((key) => <div key={key}>{tr(`rules.${key}`)}</div>)}
              </div>
            </details>

            <Button variant="secondary" size="sm" className="mt-1 w-full" onClick={handleAskAI}>
              <Sparkles className="mr-1 h-3.5 w-3.5" /> {tr('askAI')}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
