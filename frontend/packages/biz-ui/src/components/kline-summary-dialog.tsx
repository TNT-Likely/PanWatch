import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'

export interface KlineSummaryData {
  // meta (from backend)
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

function formatLocalDateTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
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
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

  // actionLabel 已由评分生成，组件内无需单独映射

  const actionStyle = (action: Action) => {
    switch (action) {
      case 'buy':
      case 'add':
        return 'bg-rose-500 text-white'
      case 'reduce':
      case 'sell':
        return 'bg-emerald-600 text-white'
      case 'hold':
        return 'bg-amber-500 text-white'
      case 'avoid':
      case 'watch':
      default:
        return 'bg-slate-500 text-white'
    }
  }

  const buildSuggestion = (s: KlineSummaryData, holding?: boolean) => {
    const scored = buildKlineSuggestion(s, holding)
    const items: Array<{ text: string; delta: number }> = []
    let localScore = 0

    const add = (text: string, delta: number) => { items.push({ text, delta }); localScore += delta }

    if (s.trend?.includes('多头')) add('均线多头排列，趋势偏强', 2)
    else if (s.trend?.includes('空头')) add('均线空头排列，趋势偏弱', -2)

    if (s.macd_status?.includes('金叉')) add('MACD 金叉，短线动能偏强', 2)
    if (s.macd_status?.includes('死叉')) add('MACD 死叉，短线动能转弱', -2)
    if (typeof s.macd_hist === 'number') add(`MACD 柱体${s.macd_hist > 0 ? '为正' : s.macd_hist < 0 ? '为负' : '接近0'}`, s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0)

    if (s.rsi_status?.includes('超卖')) add('RSI 超卖，可能存在反弹', 1)
    else if (s.rsi_status?.includes('偏强')) add('RSI 偏强，买盘占优', 1)
    else if (s.rsi_status?.includes('超买')) add('RSI 超买，注意回调风险', -1)
    else if (s.rsi_status?.includes('偏弱')) add('RSI 偏弱，短线承压', -1)

    if (s.kdj_status?.includes('金叉')) add('KDJ 金叉，短线转强', 1)
    if (s.kdj_status?.includes('死叉')) add('KDJ 死叉，短线转弱', -1)

    if (s.boll_status?.includes('突破上轨')) add('突破布林上轨，趋势强势', 1)
    else if (s.boll_status?.includes('跌破下轨')) add('跌破布林下轨，走势偏弱', -1)

    if (s.volume_trend?.includes('放量')) add('放量配合，资金参与度提升', 1)
    else if (s.volume_trend?.includes('缩量')) add('缩量，动能不足', -1)

    if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('价格接近支撑位，止跌反弹概率提升', 1)
    if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('价格接近压力位，上行空间受限', -1)

    return { ...scored, score: localScore, items }
  }

  useEffect(() => {
    if (!open || !symbol) return

    // If we already have preloaded summary, use it without refetch
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setSummary(null)

    const m = market || 'CN'
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(m)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = effectiveSummary ? buildSuggestion(effectiveSummary, hasPosition) : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>K线 / 技术指标</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {effectiveSummary?.timeframe ? `周期: ${effectiveSummary.timeframe}` : '周期: 1d'}
                  {effectiveSummary?.asof ? ` · 数据截至: ${effectiveSummary.asof}` : ''}
                  {effectiveSummary?.computed_at ? ` · 计算时间: ${formatLocalDateTime(effectiveSummary.computed_at)}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">加载中...</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">暂无数据</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="p-3 rounded-lg bg-accent/20 border border-border/30">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-[11px] px-2 py-1 rounded font-medium ${actionStyle(suggestion.action)}`}>
                    {suggestion.action_label}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? '已持仓' : '未持仓'} · score {suggestion.score}
                  </span>
                </div>
                <div className="mt-2 text-[12px] text-foreground font-medium">
                  {suggestion.signal}
                </div>

                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((it, idx) => {
                      const color =
                        it.delta > 0 ? 'text-rose-500' :
                        it.delta < 0 ? 'text-emerald-500' :
                        'text-muted-foreground'
                      return (
                        <div key={`${it.text}-${idx}`} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className="text-muted-foreground">{it.text}</span>
                          <span className={`font-mono ${color}`}>
                            {it.delta > 0 ? '+' : ''}{it.delta}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                <div className="mt-2 text-[10px] text-muted-foreground/70">
                  仅基于技术指标规则生成，非投资建议
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">
              提示：悬停指标标签可查看详细说明
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary.trend && (
                <HoverPopover
                  title="趋势（均线排列）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        趋势标签来自均线（MA5/MA10/MA20）的相对位置，基于日K收盘价计算。MA越短越敏感，越长越平滑。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">常见解读：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">多头排列</span>（MA5 &gt; MA10 &gt; MA20）：上升趋势更“顺”，回调通常先看 MA5/MA10 的支撑。</li>
                          <li><span className="font-medium text-foreground">空头排列</span>（MA5 &lt; MA10 &lt; MA20）：下降趋势占优，反弹到 MA10/MA20 往往遇到压力。</li>
                          <li><span className="font-medium text-foreground">均线交织</span>：震荡/换手期，信号更依赖成交量与关键价位。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">当前：{effectiveSummary.trend}</div>
                      {(effectiveSummary.ma5 != null || effectiveSummary.ma10 != null || effectiveSummary.ma20 != null || effectiveSummary.ma60 != null) && (
                        <div className="text-[10px] text-muted-foreground/70">
                          均线：MA5≈{effectiveSummary.ma5 != null ? effectiveSummary.ma5.toFixed(2) : '—'}；MA10≈{effectiveSummary.ma10 != null ? effectiveSummary.ma10.toFixed(2) : '—'}；MA20≈{effectiveSummary.ma20 != null ? effectiveSummary.ma20.toFixed(2) : '—'}；MA60≈{effectiveSummary.ma60 != null ? effectiveSummary.ma60.toFixed(2) : '—'}
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：均线属于滞后指标，更适合“过滤趋势”，不建议单独作为进出场依据。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      {effectiveSummary.trend}
                    </span>
                  }
                />
              )}

              {effectiveSummary.macd_status && (
                <HoverPopover
                  title="MACD（趋势/动能）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        MACD 由两条线（DIF/DEA）与柱体（hist）组成。常见口径：DIF=EMA12-EMA26，DEA=EMA(DIF,9)，hist≈(DIF-DEA)*2。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什么：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">金叉</span>：DIF 上穿 DEA，短线动能由弱转强。</li>
                          <li><span className="font-medium text-foreground">死叉</span>：DIF 下穿 DEA，短线动能由强转弱。</li>
                          <li><span className="font-medium text-foreground">柱体正/负</span>：正值通常表示多头动能占优；负值通常表示空头动能占优。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        当前：{effectiveSummary.macd_status}{effectiveSummary.macd_hist != null ? `，柱体${effectiveSummary.macd_hist > 0 ? '为正' : effectiveSummary.macd_hist < 0 ? '为负' : '接近0'} (hist≈${effectiveSummary.macd_hist.toFixed(3)})` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：MACD 在震荡区间容易频繁“假交叉”，通常需要结合趋势（均线）与量价确认。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      MACD {effectiveSummary.macd_status}
                    </span>
                  }
                />
              )}

              {effectiveSummary.rsi_status && (
                <HoverPopover
                  title="RSI（相对强弱）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        RSI 用于衡量一段时间内上涨与下跌力度的相对强弱（0-100）。这里展示的是 RSI6（近6个交易日）。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">项目内阈值：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>RSI6 &gt; 80：超买（回撤风险更高）</li>
                          <li>RSI6 70-80：偏强（动能偏多）</li>
                          <li>RSI6 &lt; 20：超卖（可能反弹，但下跌趋势中可长期超卖）</li>
                          <li>RSI6 20-30：偏弱（动能偏空）</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        当前：{effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null ? `，RSI6≈${effectiveSummary.rsi6.toFixed(0)}` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：超买不等于立刻下跌、超卖不等于立刻反弹；更可靠的用法是结合趋势和关键位看“背离/衰竭”。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      RSI {effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null && ` (${effectiveSummary.rsi6.toFixed(0)})`}
                    </span>
                  }
                />
              )}

              {effectiveSummary?.kdj_status && (
                <HoverPopover
                  title="KDJ（随机指标）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        KDJ 属于动量类指标，反映价格在一段区间内所处位置（类似随机振荡器）。常用信号是 K 与 D 的金叉/死叉。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什么：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">金叉</span>：短线转强的提示，配合上升趋势更有效。</li>
                          <li><span className="font-medium text-foreground">死叉</span>：短线转弱的提示，配合下降趋势更有效。</li>
                          <li>J 值极端（&gt;100 或 &lt;0）时，常被视为“超买/超卖”，但在强趋势里可能失真。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>当前：{effectiveSummary.kdj_status}</div>
                        {(effectiveSummary.kdj_k != null || effectiveSummary.kdj_d != null || effectiveSummary.kdj_j != null) && (
                          <div>
                            K≈{effectiveSummary.kdj_k != null ? effectiveSummary.kdj_k.toFixed(1) : '—'}{' '}
                            D≈{effectiveSummary.kdj_d != null ? effectiveSummary.kdj_d.toFixed(1) : '—'}{' '}
                            J≈{effectiveSummary.kdj_j != null ? effectiveSummary.kdj_j.toFixed(1) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：震荡行情里 KDJ 可能频繁反复，建议与支撑/压力位结合使用。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      KDJ {effectiveSummary.kdj_status}
                    </span>
                  }
                />
              )}

              {effectiveSummary?.volume_trend && (
                <HoverPopover
                  title="量能（放量/缩量）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        量能用于判断行情“是否有成交支撑”。这里的量能趋势来自 volume_ratio（当日量 / 近5日均量）。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎么解读：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">放量</span>：通常表示参与度提升；若上涨放量更利于趋势延续。</li>
                          <li><span className="font-medium text-foreground">缩量</span>：可能表示观望/衰竭；若下跌缩量，有时是抛压减弱的信号。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        当前：{effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null ? `，量比≈${effectiveSummary.volume_ratio.toFixed(1)}x` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：量能的意义需要结合价格方向（价涨量增/价涨量缩/价跌量增/价跌量缩）综合判断。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      {effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null && ` (${effectiveSummary.volume_ratio.toFixed(1)}x)`}
                    </span>
                  }
                />
              )}

              {effectiveSummary?.boll_status && (
                <HoverPopover
                  title="布林带（波动/通道）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        布林带由中轨（通常是 MA20）和上下轨（中轨±2倍标准差）组成，用于刻画价格通道与波动变化。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什么：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">突破上轨</span>：短期偏强，但也可能“冲高回落”，需结合量能确认。</li>
                          <li><span className="font-medium text-foreground">跌破下轨</span>：短期偏弱，但在恐慌下跌时也可能出现超跌反弹。</li>
                          <li>带宽收口常见于波动收敛，之后容易出现方向选择；带宽开口表示波动放大。</li>
                        </ul>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">项目内带宽阈值：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>带宽 &lt; 5：收口窄幅（更偏盘整/酝酿）</li>
                          <li>带宽 &gt; 15：开口放大（波动扩张）</li>
                          <li>其他：正常波动</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>
                          当前：{effectiveSummary.boll_status}{effectiveSummary.boll_width != null ? `，带宽≈${effectiveSummary.boll_width.toFixed(1)}%` : ''}
                        </div>
                        {(effectiveSummary.boll_upper != null || effectiveSummary.boll_mid != null || effectiveSummary.boll_lower != null) && (
                          <div>
                            上轨≈{effectiveSummary.boll_upper != null ? effectiveSummary.boll_upper.toFixed(2) : '—'}；中轨≈{effectiveSummary.boll_mid != null ? effectiveSummary.boll_mid.toFixed(2) : '—'}；下轨≈{effectiveSummary.boll_lower != null ? effectiveSummary.boll_lower.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">
                      布林 {effectiveSummary.boll_status}
                    </span>
                  }
                />
              )}

              {effectiveSummary?.kline_pattern && (
                <HoverPopover
                  title="K线形态（局部结构）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        形态来自对最近1-2根K线的形状识别（如十字星、锤子线、吞没等），属于“局部信号”。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什么：</span>
                        多数形态需要结合趋势、量能与关键位确认。比如锤子线出现在下跌末端更有意义；吞没形态更看重“前后两根K线对比”。
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">当前：{effectiveSummary.kline_pattern}</div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：单根K线形态误判率较高，建议仅作提示，不建议孤立决策。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-600 cursor-help hover:bg-amber-500/15">
                      {effectiveSummary.kline_pattern}
                    </span>
                  }
                />
              )}
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary && effectiveSummary.support != null && (
                <HoverPopover
                  title="支撑位（关键支撑区）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        支撑位可以理解为“买盘更容易出现”的价格区域。接近支撑时，价格更可能出现止跌、反弹或盘整。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">本项目如何算：</span>
                        当前弹窗里的支撑（support）来自最近20个交易日区间内的最低价（min low），属于中期级别的参考位。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎么用：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>更偏向“区域”而不是精确到1分钱的一条线，常见做法是允许一定误差（例如±1%~2%）。</li>
                          <li>接近支撑时，若出现缩量止跌/放量反弹，信号通常更可靠；若放量跌破，则支撑可能失效并转为压力。</li>
                          <li>适合用于设置止损/止盈/加减仓区间：用关键位去约束风险，而不是预测最高点最低点。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>当前：支撑≈{effectiveSummary.support.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.support > 0 && (
                          <div>
                            距离（以收盘价计）≈{(((effectiveSummary.last_close - effectiveSummary.support) / effectiveSummary.support) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close <= effectiveSummary.support * 1.02 ? '（接近支撑，评分规则会加分）' : ''}
                          </div>
                        )}
                        {(effectiveSummary.support_s != null || effectiveSummary.support_m != null || effectiveSummary.support_l != null) && (
                          <div>
                            多级别：短期(5日)≈{effectiveSummary.support_s != null ? effectiveSummary.support_s.toFixed(2) : '—'}；中期(20日)≈{effectiveSummary.support_m != null ? effectiveSummary.support_m.toFixed(2) : '—'}；长期(60日)≈{effectiveSummary.support_l != null ? effectiveSummary.support_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：支撑/压力是“统计出的关键位”，不是必然会反转的点位；趋势很强时可直接击穿。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600 cursor-help hover:bg-emerald-500/15">
                      支撑 {effectiveSummary.support.toFixed(2)}
                  </span>
                }
              />
              )}
              {effectiveSummary && effectiveSummary.resistance != null && (
                <HoverPopover
                  title="压力位（关键压力区）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什么：</span>
                        压力位可以理解为“卖盘更容易出现”的价格区域。接近压力时，上行更容易受阻、回落或进入震荡。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">本项目如何算：</span>
                        当前弹窗里的压力（resistance）来自最近20个交易日区间内的最高价（max high），属于中期级别的参考位。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎么用：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>越接近压力，追涨的性价比越低；更常见的策略是等“放量突破后回踩不破”再考虑。</li>
                          <li>若放量突破压力并站稳，原压力往往会“角色互换”变成新的支撑。</li>
                          <li>压力附近可用来规划分批止盈/减仓，或观察是否出现量价背离、冲高回落等风险信号。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>当前：压力≈{effectiveSummary.resistance.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.resistance > 0 && (
                          <div>
                            距离（以收盘价计）≈{(((effectiveSummary.resistance - effectiveSummary.last_close) / effectiveSummary.resistance) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close >= effectiveSummary.resistance * 0.98 ? '（接近压力，评分规则会扣分）' : ''}
                          </div>
                        )}
                        {(effectiveSummary.resistance_s != null || effectiveSummary.resistance_m != null || effectiveSummary.resistance_l != null) && (
                          <div>
                            多级别：短期(5日)≈{effectiveSummary.resistance_s != null ? effectiveSummary.resistance_s.toFixed(2) : '—'}；中期(20日)≈{effectiveSummary.resistance_m != null ? effectiveSummary.resistance_m.toFixed(2) : '—'}；长期(60日)≈{effectiveSummary.resistance_l != null ? effectiveSummary.resistance_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：突破是否有效，往往取决于“是否放量 + 是否能站稳/回踩确认”。单靠刺穿一瞬间容易假突破。
                      </div>
                    </div>
                  }
                  trigger={
                    <span className="px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 cursor-help hover:bg-rose-500/15">
                      压力 {effectiveSummary.resistance.toFixed(2)}
                  </span>
                }
              />
              )}
            </div>

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && (
                  <HoverPopover
                    title="5日涨跌幅（短期动量）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什么：</span>
                          5日涨跌幅表示最近5个交易日的整体收益率，用来快速观察短期动量强弱。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本项目如何算：</span>
                          使用“今日收盘”对比“5个交易日前收盘”的变化：（Close[t]-Close[t-5]) / Close[t-5]。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎么解读：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>正值：短期偏强；配合放量/多头趋势时，更可能延续。</li>
                            <li>负值：短期偏弱；若同时均线空头、MACD死叉，风险更大。</li>
                            <li>过大的正涨幅也可能意味着“短期过热”，要防回撤；更建议结合支撑/压力位设定风控。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          当前：{effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        5日{' '}
                        <span className={effectiveSummary.change_5d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.change_20d != null && (
                  <HoverPopover
                    title="20日涨跌幅（波段/一月动量）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什么：</span>
                          20日涨跌幅接近一个交易月的整体收益率，更偏向“波段趋势”的表现。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本项目如何算：</span>
                          使用“今日收盘”对比“20个交易日前收盘”的变化：（Close[t]-Close[t-20]) / Close[t-20]。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎么解读：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>正值且趋势多头：通常更偏顺势；回调时更关注支撑位与成交量。</li>
                            <li>负值且趋势空头：通常更偏逆风；反弹到压力位附近更容易受阻。</li>
                            <li>5日与20日分歧：可能代表“短期反弹/回调”发生在更大的趋势里，需谨慎辨别是否反转。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          当前：{effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        20日{' '}
                        <span className={effectiveSummary.change_20d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.amplitude != null && (
                  <HoverPopover
                    title="振幅（波动强度）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什么：</span>
                          振幅描述当天高低价之间的波动范围，用来衡量“波动有多大”。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本项目如何算：</span>
                          今日振幅≈(High-Low)/Low。数值越大，表示盘中波动越剧烈、风险与机会都更大。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎么解读：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>高振幅常见于放量突破、恐慌下跌、消息驱动等；需要配合量能和趋势判断“是扩张还是崩盘”。</li>
                            <li>低振幅常见于盘整/收敛期；若布林带同时收口，后续更可能出现方向选择。</li>
                            <li>振幅高时更建议降低仓位/更严格止损；避免用“同一套止损距离”应对不同波动。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 space-y-1">
                          <div>当前：{effectiveSummary.amplitude.toFixed(2)}%</div>
                          {effectiveSummary.amplitude_avg5 != null && (
                            <div>近5日均值：{effectiveSummary.amplitude_avg5.toFixed(2)}%</div>
                          )}
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        振幅: {effectiveSummary.amplitude.toFixed(2)}%
                      </span>
                    }
                  />
                )}
              </div>
            )}

            <details className="group">
              <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                建议/评分规则说明 <span className="text-[10px]">(点击展开)</span>
              </summary>
              <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 space-y-2">
                <div className="font-medium text-foreground">建议规则（按是否持仓）</div>
                <div className="space-y-1">
                  <div>未持仓：score ≥ 3 → 买入；score ≤ -2 → 回避；其他 → 观望</div>
                  <div>已持仓：score ≥ 3 → 加仓；score ≥ 1 → 持有；score ≤ -3 → 卖出；score ≤ -1 → 减仓；其他 → 观望</div>
                </div>
                <div className="font-medium text-foreground">评分规则（各项累加，0 为中性）</div>
                <div className="space-y-1">
                  <div>趋势（均线）：多头排列 +2；空头排列 -2</div>
                  <div>MACD：金叉 +2；死叉 -2；柱体为正 +1；柱体为负 -1</div>
                  <div>RSI：超卖 +1；偏强 +1；超买 -1；偏弱 -1</div>
                  <div>KDJ：金叉 +1；死叉 -1</div>
                  <div>布林：突破上轨 +1；跌破下轨 -1</div>
                  <div>量能：放量 +1；缩量 -1</div>
                  <div>支撑/压力：收盘价 ≤ 支撑×1.02 → +1；收盘价 ≥ 压力×0.98 → -1</div>
                </div>
              </div>
            </details>

          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
