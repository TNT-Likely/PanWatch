import ReactMarkdown from 'react-markdown'
import { RefreshCw } from 'lucide-react'
import { type DeepAnalysisResult, type HistoryComparisonResponse } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) return '--'
  return value.toFixed(digits)
}

export function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

export function formatMarketCap(value: number | null | undefined, market?: string): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const m = String(market || '').toUpperCase()
  const abs = Math.abs(n)

  // 腾讯 A 股字段常见为“亿元”口径（如 808 表示 808 亿元）
  if (m === 'CN' && abs > 0 && abs < 100000) {
    return `${n.toFixed(2)}亿元`
  }

  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿元`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万元`
  return `${n.toFixed(0)}元`
}

export function formatTime(isoTime?: string): string {
  if (!isoTime) return ''
  const d = new Date(isoTime)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function parseToMs(input?: string): number | null {
  if (!input) return null
  const d = new Date(input)
  if (!isNaN(d.getTime())) return d.getTime()
  const m = input.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0)
  return isNaN(dt.getTime()) ? null : dt.getTime()
}

export function parseSuggestionJson(raw: unknown): Record<string, any> | null {
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s) return null
  const candidates: string[] = [s]
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence?.[1]) candidates.unshift(fence[1].trim())
  if (/^json\s*[\r\n]/i.test(s)) candidates.unshift(s.replace(/^json\s*[\r\n]/i, '').trim())
  for (const c of candidates) {
    if (!c) continue
    const direct = c
    const sliceStart = c.indexOf('{')
    const sliceEnd = c.lastIndexOf('}')
    const sliced = sliceStart >= 0 && sliceEnd > sliceStart ? c.slice(sliceStart, sliceEnd + 1) : ''
    for (const text of [direct, sliced]) {
      if (!text || !text.startsWith('{') || !text.endsWith('}')) continue
      try {
        const obj = JSON.parse(text)
        if (obj && typeof obj === 'object') return obj as Record<string, any>
      } catch {
        // try next candidate
      }
    }
  }
  return null
}

export function normalizeSuggestionAction(action?: string, actionLabel?: string): string {
  const a = String(action || '').trim().toLowerCase()
  const l = String(actionLabel || '').trim()
  if (a === 'buy/add' || a === 'add/buy') return /加仓|增持|补仓/.test(l) ? 'add' : 'buy'
  if (a === 'sell/reduce' || a === 'reduce/sell') return /减仓|减持/.test(l) ? 'reduce' : 'sell'
  return a || 'watch'
}

export function pickSuggestionText(raw: unknown, field: 'signal' | 'reason'): string {
  const plain = String(raw || '').trim()
  const obj = parseSuggestionJson(plain)
  if (obj) {
    const v = String(obj[field] || '').trim()
    if (v) return v
    if (field === 'reason') {
      const rv = String(obj['raw'] || '').trim()
      if (rv) return rv
    }
    return ''
  }
  return plain
}

export function normalizeTextList(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(x => String(x || '').trim()).filter(Boolean)
  const s = String(raw || '').trim()
  if (!s) return []
  const bySep = s.split(/[；;、|]/).map(x => x.trim()).filter(Boolean)
  return bySep.length > 1 ? bySep : [s]
}

export function markdownToPlainText(input?: string): string {
  const raw = String(input || '').trim()
  if (!raw) return ''
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\*\*|__|\*|_/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function firstNonEmptyText(...vals: unknown[]): string {
  for (const v of vals) {
    const s = String(v || '').trim()
    if (s) return s
  }
  return ''
}

export function buildShareTechnicalRisks(kline: KlineSummary | null): string[] {
  if (!kline) return []
  const out: string[] = []
  const rsi = String(kline.rsi_status || '')
  const macd = `${kline.macd_cross || ''} ${kline.macd_status || ''}`
  const vol = String(kline.volume_trend || '')
  if (rsi.includes('超买')) out.push('短线过热回撤风险')
  if (rsi.includes('超卖')) out.push('弱势延续风险')
  if (macd.includes('死叉')) out.push('趋势转弱风险')
  if (macd.includes('顶背离')) out.push('动能背离风险')
  if (vol.includes('放量')) out.push('波动放大风险')
  return out.slice(0, 3)
}

export function TechnicalIndicatorStrip(props: {
  klineSummary: KlineSummary | null
  technicalSuggestion: SuggestionInfo | null
  stockName: string
  stockSymbol: string
  market: string
  hasPosition: boolean
  score?: number
  evidence?: Array<{ text: string; delta: number }>
}) {
  const { klineSummary, technicalSuggestion, stockName, stockSymbol, market, hasPosition, score, evidence = [] } = props
  if (!klineSummary) {
    return <div className="text-body-sm text-muted-foreground py-3">暂无技术指标</div>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-body-sm text-muted-foreground">技术指标建议</span>
        <SuggestionBadge
          suggestion={technicalSuggestion}
          stockName={stockName}
          stockSymbol={stockSymbol}
          market={market}
          kline={klineSummary}
          hasPosition={hasPosition}
        />
        <TechnicalBadge label={`评分 ${Number(score ?? 0).toFixed(1)}`} tone="neutral" size="xs" className="text-foreground" />
      </div>
      {evidence.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-mini">
          {evidence.slice(0, 6).map((item, idx) => (
            <TechnicalBadge
              key={`${item.text}-${idx}`}
              label={`${item.text} ${item.delta > 0 ? `+${item.delta}` : item.delta}`}
              tone={item.delta > 0 ? 'bullish' : item.delta < 0 ? 'bearish' : 'neutral'}
              size="xs"
            />
          ))}
        </div>
      )}
      <KlineIndicators summary={klineSummary as any} />
    </div>
  )
}

export const DEEP_DECISION_COLOR: Record<string, string> = {
  buy: 'text-stock-down dark:text-stock-down',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-stock-up dark:text-stock-up',
}

export const DEEP_STAGE_LABEL: Record<string, string> = {
  market: '技术分析师',
  social: '情绪分析师',
  news: '新闻分析师',
  fundamentals: '基本面分析师',
}

export function DeepAnalysisSection({
  loading,
  loaded,
  result,
  history,
  historyLoading,
  showAnalyst,
  setShowAnalyst,
  showDebate,
  setShowDebate,
  onRefresh,
}: {
  loading: boolean
  loaded: boolean
  result: DeepAnalysisResult | null
  history: HistoryComparisonResponse | null
  historyLoading: boolean
  showAnalyst: boolean
  setShowAnalyst: (v: boolean) => void
  showDebate: boolean
  setShowDebate: (v: boolean) => void
  onRefresh: () => void
}) {
  if (loading && !loaded) {
    return (
      <div className="card p-6 text-center text-body-sm text-muted-foreground">
        <span className="inline-block w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2 align-middle" />
        正在加载深度分析报告...
      </div>
    )
  }
  if (!result && !history?.items?.length) {
    return (
      <div className="card p-6 text-center text-body-sm text-muted-foreground space-y-2">
        <div>暂无深度分析报告</div>
        <div className="text-caption text-muted-foreground">
          可在持仓 / 自选页点击 🧠 深度分析按钮触发
        </div>
      </div>
    )
  }

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const costUsd = rawData.cost_usd

  return (
    <div className="space-y-3 text-body">
      <div className="flex items-center justify-between gap-2">
        <div className="text-caption text-muted-foreground">
          TradingAgents 深度{result?.timestamp ? ` · ${result.timestamp.slice(0, 16).replace('T', ' ')}` : ''}
        </div>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-caption" onClick={onRefresh} disabled={loading || historyLoading}>
          <RefreshCw className={`w-3.5 h-3.5 ${loading || historyLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {sug && (
        <div className="rounded-lg bg-accent/30 p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span className={`text-headline font-bold ${DEEP_DECISION_COLOR[sug.action] || ''}`}>
              {sug.action_label}
            </span>
            {typeof sug.confidence === 'number' && (
              <span className="text-body-sm text-muted-foreground">
                置信度 {sug.confidence.toFixed(1)} / 10
              </span>
            )}
          </div>
          {sug.reason && <div className="text-body-sm text-foreground/80">{sug.reason.slice(0, 240)}</div>}
          {typeof costUsd === 'number' && (
            <div className="text-mini text-muted-foreground mt-2">成本:${costUsd.toFixed(4)}</div>
          )}
        </div>
      )}

      <DeepHistoryComparison history={history} loading={historyLoading} />

      {result?.content && (
        <div className="rounded-lg border border-border/50 p-4">
          <div className="prose prose-sm dark:prose-invert max-w-none break-words">
            <ReactMarkdown>{result.content}</ReactMarkdown>
          </div>
        </div>
      )}

      {result && (
        <div>
          <button
            className="text-body-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowAnalyst(!showAnalyst)}
          >
            {showAnalyst ? '▼' : '▶'} 4 位分析师报告
          </button>
          {showAnalyst && (
            <div className="space-y-3 mt-2 pl-3 border-l-2 border-border/40">
              {(['market', 'social', 'news', 'fundamentals'] as const).map((k) => {
                const text = (reports as unknown as Record<string, string>)[k] || ''
                if (!text) return null
                return (
                  <details key={k} open className="text-body-sm">
                    <summary className="font-medium cursor-pointer">{DEEP_STAGE_LABEL[k] || k}</summary>
                    <div className="mt-2 text-caption text-foreground/80 whitespace-pre-wrap">
                      {text.slice(0, 1500)}
                      {text.length > 1500 && '... (截断)'}
                    </div>
                  </details>
                )
              })}
            </div>
          )}
        </div>
      )}

      {debate && debate.history && (
        <div>
          <button
            className="text-body-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowDebate(!showDebate)}
          >
            {showDebate ? '▼' : '▶'} 看多看空辩论
          </button>
          {showDebate && (
            <div className="mt-2 pl-3 border-l-2 border-border/40 text-caption text-foreground/80 whitespace-pre-wrap max-h-96 overflow-y-auto">
              {debate.history}
              {debate.judge_decision && (
                <>
                  <div className="font-medium mt-3 mb-1">研究主管裁决:</div>
                  <div>{debate.judge_decision}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="text-mini text-muted-foreground italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,仅供学习研究参考,不构成任何投资建议。
      </div>
    </div>
  )
}

export function DeepHistoryComparison({
  history,
  loading,
}: {
  history: HistoryComparisonResponse | null
  loading: boolean
}) {
  if (loading && !history) {
    return (
      <div className="rounded-lg border border-border/40 p-3 text-caption text-muted-foreground text-center">
        历史对比加载中...
      </div>
    )
  }
  if (!history || history.items.length === 0) return null

  const stats = history.stats
  const fmtPct = (v: number | null): string => (v == null ? '-' : `${(v * 100).toFixed(0)}%`)
  const fmtRet = (v: number | null): string => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const retCls = (v: number | null): string =>
    v == null ? 'text-muted-foreground' : v > 0 ? 'text-stock-down dark:text-stock-down' : v < 0 ? 'text-stock-up dark:text-stock-up' : 'text-muted-foreground'

  return (
    <div className="rounded-lg border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-body-sm font-medium">历史决策 vs 实际涨跌</div>
        <div className="text-mini text-muted-foreground">仅基于满 20 个交易日的决策统计</div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-caption">
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">总命中率</div>
          <div className="font-semibold">{fmtPct(stats.overall_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">买入 ({stats.buy_count})</div>
          <div className="font-semibold text-stock-down dark:text-stock-down">{fmtPct(stats.buy_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">卖出 ({stats.sell_count})</div>
          <div className="font-semibold text-stock-up dark:text-stock-up">{fmtPct(stats.sell_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">平均 20 日收益</div>
          <div className={`font-semibold ${retCls(stats.avg_return_20d_pct)}`}>{fmtRet(stats.avg_return_20d_pct)}</div>
        </div>
      </div>
      <div className="overflow-x-auto -mx-1 mt-2">
        <table className="w-full text-caption">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border/40">
              <th className="text-left px-1 py-1 font-normal">日期</th>
              <th className="text-left px-1 py-1 font-normal">决策</th>
              <th className="text-right px-1 py-1 font-normal">分析价</th>
              <th className="text-right px-1 py-1 font-normal">1日</th>
              <th className="text-right px-1 py-1 font-normal">5日</th>
              <th className="text-right px-1 py-1 font-normal">20日</th>
              <th className="text-center px-1 py-1 font-normal">命中</th>
            </tr>
          </thead>
          <tbody>
            {history.items.map((item, i) => (
              <tr key={`${item.analysis_date}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.analysis_date}</td>
                <td className="px-1 py-1">
                  <span className={DEEP_DECISION_COLOR[item.action] || ''}>{item.action_label}</span>
                  {typeof item.confidence === 'number' && (
                    <span className="text-muted-foreground text-mini ml-1">({item.confidence.toFixed(1)})</span>
                  )}
                </td>
                <td className="px-1 py-1 text-right text-foreground/80">{item.price_at_analysis ?? '-'}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_1d_pct)}`}>{fmtRet(item.return_1d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_5d_pct)}`}>{fmtRet(item.return_5d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_20d_pct)}`}>{fmtRet(item.return_20d_pct)}</td>
                <td className="px-1 py-1 text-center">
                  {item.hit_20d == null ? <span className="text-muted-foreground">-</span> : item.hit_20d ? '✓' : '✗'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
