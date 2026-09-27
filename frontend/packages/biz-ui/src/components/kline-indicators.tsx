import { useTranslation } from 'react-i18next'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

type Translate = (key: string, options?: Record<string, unknown>) => string

export function localizeTechnicalStatus(value: string | null | undefined, tr: Translate): string {
  if (!value) return ''
  const rules: Array<[string, string]> = [
    ['多头', 'bullish'], ['空头', 'bearish'], ['交织', 'mixed'],
    ['金叉', 'goldenCross'], ['死叉', 'deathCross'], ['超买', 'overbought'],
    ['超卖', 'oversold'], ['偏强', 'strong'], ['偏弱', 'weak'],
    ['放量', 'volumeUp'], ['缩量', 'volumeDown'], ['突破上轨', 'upperBreak'],
    ['跌破下轨', 'lowerBreak'],
  ]
  const match = rules.find(([source]) => value.includes(source))
  return match ? tr(`statuses.${match[1]}`) : value
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  const { t, i18n } = useTranslation('bizUi')
  const tr: Translate = (key, options) =>
    (t as unknown as Translate)(`kline.${key}`, options)
  const english = (i18n.resolvedLanguage || i18n.language).toLowerCase().startsWith('en')
  const status = (value: string | null | undefined) => localizeTechnicalStatus(value, tr)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
          <HoverPopover title={tr('indicators.trend.title')} content={<div>{tr('indicators.trend.description')}</div>} trigger={<TechnicalBadge label={status(s.trend)} tone="neutral" help />} />
        )}
        {s.macd_status && (
          <HoverPopover title={tr('indicators.macd.title')} content={<div>{tr('indicators.macd.description')}</div>} trigger={<TechnicalBadge label={`MACD ${status(s.macd_status)}`} tone="neutral" help />} />
        )}
        {s.rsi_status && (
          <HoverPopover
            title={tr('indicators.rsi.title')}
            content={<div>{tr('indicators.rsi.description')}</div>}
            trigger={<TechnicalBadge label={`RSI ${status(s.rsi_status)}${s.rsi6 != null ? ` (${s.rsi6.toFixed(0)})` : ''}`} tone={s.rsi_status.includes('超买') ? 'bullish' : s.rsi_status.includes('超卖') ? 'bearish' : 'neutral'} help />}
          />
        )}
        {s.kdj_status && (
          <HoverPopover title={tr('indicators.kdj.title')} content={<div>{tr('indicators.kdj.description')}</div>} trigger={<TechnicalBadge label={`KDJ ${status(s.kdj_status)}`} tone="neutral" help />} />
        )}
        {s.volume_trend && (
          <HoverPopover
            title={tr('indicators.volume.title')}
            content={<div>{tr('indicators.volume.description')}</div>}
            trigger={<TechnicalBadge label={`${status(s.volume_trend)}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`} tone={s.volume_trend.includes('放量') ? 'warning' : s.volume_trend.includes('缩量') ? 'info' : 'neutral'} help />}
          />
        )}
        {s.boll_status && (
          <HoverPopover
            title={tr('indicators.boll.title')}
            content={<div>{tr('indicators.boll.description')}</div>}
            trigger={<TechnicalBadge label={`BOLL ${status(s.boll_status)}`} tone={s.boll_status.includes('突破上轨') ? 'bullish' : s.boll_status.includes('跌破下轨') ? 'bearish' : 'neutral'} help />}
          />
        )}
        {s.kline_pattern && (
          <HoverPopover title={tr('indicators.pattern.title')} content={<div>{tr('indicators.pattern.description')}</div>} trigger={<TechnicalBadge label={english ? tr('indicators.pattern.title') : s.kline_pattern} tone="warning" help />} />
        )}
      </div>

      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover title={tr('indicators.support.title')} content={<div>{tr('indicators.support.description')}</div>} trigger={<TechnicalBadge label={tr('indicators.support.label', { value: s.support.toFixed(2) })} tone="bearish" help />} />
        )}
        {s.resistance != null && (
          <HoverPopover title={tr('indicators.resistance.title')} content={<div>{tr('indicators.resistance.description')}</div>} trigger={<TechnicalBadge label={tr('indicators.resistance.label', { value: s.resistance.toFixed(2) })} tone="bullish" help />} />
        )}
      </div>
    </div>
  )
}
