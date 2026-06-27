import { useEffect, useState } from 'react'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'
import { technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'
import { insightApi, type ChanEmotionStrategyResult } from '@panwatch/api/insight'

interface ChanEmotionStrategyPanelProps {
  symbol: string
  market: string
  hasPosition?: boolean
}

function trendLabel(trend: string): string {
  switch (trend) {
    case 'trend_up':
      return '上升趋势'
    case 'trend_down':
      return '下降趋势'
    case 'consolidation':
      return '盘整'
    default:
      return '待识别'
  }
}

function emotionTone(phase: string): 'bullish' | 'bearish' | 'neutral' {
  if (phase === 'profit_effect') return 'bullish'
  if (phase === 'loss_effect') return 'bearish'
  return 'neutral'
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return value.toFixed(digits)
}

export function ChanEmotionStrategyPanel({
  symbol,
  market,
  hasPosition = false,
}: ChanEmotionStrategyPanelProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<ChanEmotionStrategyResult | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || !symbol) return
    let cancelled = false
    setLoading(true)
    setError('')
    insightApi
      .chanEmotionStrategy(symbol, { market, holding: hasPosition })
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message || '加载失败')
          setData(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, symbol, market, hasPosition])

  const brief = data
    ? `${data.action_label} · 赢面${data.win_rate}% · ${data.emotion_label}`
    : '缠论几何+养家心法：多级别联立、背驰触发、情绪仓位'

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-[11px] text-muted-foreground"
      >
        <span>缠论情绪博弈策略</span>
        <span>{open ? '收起 ▾' : '展开 ▸'}</span>
      </button>

      {!open && (
        <div className="mt-1 text-[10px] text-muted-foreground line-clamp-2">{brief}</div>
      )}

      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
          {loading && <div className="text-[11px] text-muted-foreground">分析中...</div>}
          {error && <div className="text-[11px] text-rose-500">{error}</div>}
          {data && !loading && (
            <>
              <div className="rounded bg-accent/15 p-2 text-[11px] leading-relaxed text-muted-foreground">
                以 30 分钟为主操作级别，5 分钟精确定位，日线定方向；形态识别为基础，动力学背驰为触发，市场情绪定仓位。
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <TechnicalBadge
                  label={data.action_label}
                  tone={technicalToneFromSuggestionAction(data.action, data.action_label)}
                  size="sm"
                />
                <TechnicalBadge
                  label={`赢面 ${data.win_rate}%`}
                  tone={data.win_rate >= 70 ? 'bullish' : data.win_rate < 60 ? 'bearish' : 'neutral'}
                  size="xs"
                />
                <TechnicalBadge
                  label={data.emotion_label.split('（')[0]}
                  tone={emotionTone(data.emotion_phase)}
                  size="xs"
                />
              </div>

              <div className="text-[11px] text-foreground font-medium">{data.signal}</div>
              <div className="text-[10px] text-muted-foreground leading-relaxed">{data.reason}</div>

              {data.decision_explanation && (
                <div className="rounded border border-primary/15 bg-primary/5 px-2 py-1.5 text-[10px] leading-relaxed text-muted-foreground">
                  <div className="mb-1 text-[11px] font-medium text-foreground/80">信号推导</div>
                  {data.decision_explanation}
                </div>
              )}

              <div className="grid grid-cols-3 gap-2">
                {data.levels.map((lvl) => (
                  <div key={lvl.timeframe} className="rounded bg-accent/15 px-2 py-1.5">
                    <div className="text-[10px] text-muted-foreground">{lvl.label}</div>
                    <div className="text-[11px]">{trendLabel(lvl.trend)}</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">
                      笔 {lvl.stroke_count} · K {lvl.bar_count}
                    </div>
                    {lvl.pivot && (
                      <div className="text-[10px] font-mono text-muted-foreground">
                        ZD {fmt(lvl.pivot.zd)} / ZG {fmt(lvl.pivot.zg)}
                      </div>
                    )}
                    {lvl.bar_count === 0 && (
                      <div className="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5">
                        {lvl.timeframe === '1d' ? '日线数据暂不可用' : '分钟数据暂不可用'}
                      </div>
                    )}
                    {lvl.signal_tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {lvl.signal_tags.slice(0, 2).map((tag) => (
                          <span
                            key={tag}
                            className="rounded bg-primary/10 px-1 py-0.5 text-[9px] text-primary"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-3 gap-2 text-[11px]">
                <div className="rounded border border-border/40 px-2 py-1.5">
                  <div className="text-[10px] text-muted-foreground">建议仓位</div>
                  <div>{data.position_label}</div>
                </div>
                <div className="rounded border border-border/40 px-2 py-1.5">
                  <div className="text-[10px] text-muted-foreground">止损参考</div>
                  <div className="font-mono">{fmt(data.stop_loss)}</div>
                </div>
                <div className="rounded border border-border/40 px-2 py-1.5">
                  <div className="text-[10px] text-muted-foreground">目标参考</div>
                  <div className="font-mono">{fmt(data.target_price)}</div>
                </div>
              </div>

              {data.invalidation && (
                <div className="rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-700 dark:text-amber-400">
                  失效条件：{data.invalidation}
                </div>
              )}

              <div className="rounded bg-accent/10 px-2 py-1.5 text-[10px] text-muted-foreground">
                <div className="text-foreground/80 mb-1">Agent 执行指令</div>
                {data.agent_instruction}
              </div>

              <ul className="space-y-1 text-[10px] text-muted-foreground/90 list-disc pl-4">
                {data.human_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}
