import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { KlineSummaryDialog } from '@/components/kline-summary-dialog'
import { KlineIndicators } from '@/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'

export interface SuggestionInfo {
  action: string  // buy/add/reduce/sell/hold/watch
  action_label: string
  signal: string
  reason: string
  should_alert: boolean
  raw?: string
  // 建议池新增字段
  agent_name?: string     // intraday_monitor/daily_report/premarket_outlook
  agent_label?: string    // 盘中监测/盘后日报/盘前分析
  created_at?: string     // ISO 时间戳
  is_expired?: boolean    // 是否已过期
  prompt_context?: string // Prompt 上下文
  ai_response?: string    // AI 原始响应
  meta?: Record<string, any>
}

export interface KlineSummary {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  trend: string
  macd_status: string
  macd_cross?: string
  macd_cross_days?: number
  recent_5_up: number
  change_5d: number | null
  change_20d: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60?: number | null
  // RSI
  rsi6?: number | null
  rsi_status?: string
  // KDJ
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  // 布林带
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_status?: string
  // 量能
  volume_ratio?: number | null
  volume_trend?: string
  // 振幅
  amplitude?: number | null
  // 多级支撑压力
  support: number | null
  resistance: number | null
  support_s?: number | null
  support_m?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  // K线形态
  kline_pattern?: string
}

interface SuggestionBadgeProps {
  suggestion: SuggestionInfo | null
  stockName?: string
  stockSymbol?: string
  kline?: KlineSummary | null
  showFullInline?: boolean  // 是否在行内显示完整信息（Dashboard 模式）
  market?: string           // 市场（用于技术指标弹窗）
  hasPosition?: boolean     // 是否持仓（用于技术指标弹窗）
}

const actionColors: Record<string, string> = {
  // 盘中监测
  buy: 'bg-rose-500 text-white',
  add: 'bg-rose-400 text-white',
  reduce: 'bg-emerald-500 text-white',
  sell: 'bg-emerald-600 text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  // 盘前分析
  alert: 'bg-blue-500 text-white',  // 设置预警
  // 盘后日报
  avoid: 'bg-red-600 text-white',  // 暂时回避
}

const actionLabels: Record<string, string> = {
  buy: '买入',
  add: '加仓',
  reduce: '减仓',
  sell: '卖出',
  hold: '持有',
  watch: '观望',
  avoid: '回避',
}

// 将各种同义中文/英文文案归一到统一的动作枚举，便于颜色和标签一致
function normalizeAction(action?: string, label?: string): keyof typeof actionColors | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  // 英文或枚举
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'

  // 中文同义
  if (/买入|买|建仓/.test(raw)) return 'buy'
  if (/加仓|增持|补仓/.test(raw)) return 'add'
  if (/减仓|减持/.test(raw)) return 'reduce'
  if (/清仓|卖出|止损|卖/.test(raw)) return 'sell'
  if (/持有|持仓/.test(raw)) return 'hold'
  if (/观望|中性|等待/.test(raw)) return 'watch'
  if (/回避|规避|避免/.test(raw)) return 'avoid'
  return null
}

// 格式化建议时间（自动转换为本地时区，只显示时:分）
function formatSuggestionTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    // 检查日期是否有效
    if (isNaN(date.getTime())) return ''
    // 使用本地时区显示
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

// 格式化完整日期时间（本地时区）
function formatSuggestionDateTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

function formatKlineMeta(meta?: Record<string, any>): string {
  if (!meta) return ''
  const computedAt = meta?.kline_meta?.computed_at
  const asof = meta?.kline_meta?.asof
  const parts: string[] = []
  if (asof) parts.push(`K线截止 ${asof}`)
  if (computedAt) parts.push(`计算 ${formatSuggestionTime(computedAt)}`)
  return parts.join(' · ')
}

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false,
  market = 'CN',
  hasPosition = false,
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)

  if (!suggestion && !kline) return null

  // Dashboard 模式：行内显示完整信息（仅建议 badge）
  if (showFullInline) {
    if (!suggestion) return null
    const normalized = normalizeAction(suggestion.action, suggestion.action_label)
    const colorClass = normalized ? (actionColors[normalized] || 'bg-slate-500 text-white') : 'bg-slate-500 text-white'
    const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技术指标'
    const aiLabel = normalized ? (actionLabels[normalized] || suggestion.action_label) : (suggestion.action_label || '观望')
    const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
    const techColor = tech ? (actionColors[tech.action] || 'bg-slate-500 text-white') : 'bg-slate-500 text-white'
    const timeStr = formatSuggestionTime(suggestion.created_at)
    const klineMetaStr = formatKlineMeta(suggestion.meta)
    return (
      <>
        <div className="pt-3 border-t border-border/30">
          <div className="flex items-start gap-3">
            <div className="shrink-0 flex items-center gap-2">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  if (suggestion.agent_label === '技术指标') setKlineDialogOpen(true)
                  else setDialogOpen(true)
                }}
                className={`relative text-[13px] px-3 py-1.5 rounded font-medium hover:opacity-80 transition-opacity whitespace-nowrap ${colorClass} ${suggestion.is_expired ? 'opacity-50' : ''}`}
                title="点击查看建议详情"
              >
                {aiLabel}
                {isAI && (
                  <span className="pointer-events-none absolute top-0 left-0 transform -translate-x-1/2 -translate-y-1/2 text-[10px] leading-none px-1.5 py-[2px] rounded-sm bg-primary text-white uppercase shadow-sm ring-1 ring-black/20">
                    AI
                  </span>
                )}
              </button>
              {isAI && (
                <button
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  className={`text-[13px] px-3 py-1.5 rounded font-medium hover:opacity-80 transition-opacity ${techColor}`}
                  title="点击查看技术面详情"
                >
                  {tech ? tech.action_label : '观望'}
                </button>
              )}
            </div>
            <div className="flex-1 min-w-0">
              {suggestion.signal && (
                <p className="text-[12px] font-medium text-foreground mb-0.5">{suggestion.signal}</p>
              )}
              {suggestion.reason ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.reason}</p>
              ) : suggestion.raw && !suggestion.signal ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.raw}</p>
              ) : null}

              {(suggestion.agent_label || timeStr) && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  来源: {suggestion.agent_label || (isAI ? 'AI' : '未知')}
                  {timeStr && ` · ${timeStr}`}
                  {suggestion.is_expired && <span className="ml-1 text-amber-600">(已过期)</span>}
                </div>
              )}

              {klineMetaStr && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  {klineMetaStr}
                </div>
              )}
            </div>
          </div>
        </div>

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <span className={`relative inline-flex text-[13px] px-3 py-1.5 rounded font-medium whitespace-nowrap ${colorClass}`}>
                  {aiLabel}
                  {isAI && (
                    <span className="pointer-events-none absolute top-0 left-0 transform -translate-x-1/2 -translate-y-1/2 text-[10px] leading-none px-1.5 py-[2px] rounded-sm bg-primary text-white uppercase shadow-sm ring-1 ring-black/20">
                      AI
                    </span>
                  )}
                </span>
                {/* AI 标签已前置到按钮文案，不再重复 */}
                {stockName && (
                  <span className="text-[14px] font-normal text-muted-foreground">
                    {stockName} {stockSymbol && `(${stockSymbol})`}
                  </span>
                )}
              </DialogTitle>
              {/* 来源信息 */}
              {(suggestion.agent_label || suggestion.created_at) && (
                <div className="text-[11px] text-muted-foreground/70 mt-1">
                  来源: {suggestion.agent_label || '未知'}
                  {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                  {suggestion.is_expired && <span className="ml-2 text-amber-500">(已过期)</span>}
                </div>
              )}
            </DialogHeader>

            <div className="space-y-4">
              {/* 信号 */}
              {suggestion.signal && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                  <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
                </div>
              )}

              {/* 理由 */}
              {(suggestion.reason || suggestion.raw) && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                  <p className="text-[13px] text-foreground">
                    {suggestion.reason || suggestion.raw}
                  </p>
                </div>
              )}

              {/* 技术指标 */}
              {kline && (
                <div className="space-y-3">
                  <div className="text-[11px] text-muted-foreground">技术指标</div>
                  <KlineIndicators summary={kline as any} />
                </div>
              )}

              {/* AI 原始响应 */}
              {suggestion.ai_response && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">AI 响应</div>
                  <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto">
                    {suggestion.ai_response}
                  </div>
                </div>
              )}

              {/* Prompt 上下文 */}
              {suggestion.prompt_context && (
                <details className="group">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                    Prompt 上下文 <span className="text-[10px]">(点击展开)</span>
                  </summary>
                  <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto">
                    {suggestion.prompt_context}
                  </div>
                </details>
              )}
            </div>
          </DialogContent>
        </Dialog>
        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  // 仅展示技术指标（无建议）
  if (!suggestion && kline) {
    return (
      <>
        <div className="inline-flex flex-col items-start gap-0.5">
          <button
            onClick={(e) => {
              e.stopPropagation()
              setKlineDialogOpen(true)
            }}
            className="text-[10px] px-1.5 py-0.5 rounded font-medium cursor-pointer hover:opacity-80 transition-opacity bg-accent/50 text-muted-foreground"
            title="点击查看技术指标"
          >
            指标
          </button>
        </div>

        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market || 'CN'}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  if (!suggestion) return null
  const normalized = normalizeAction(suggestion.action, suggestion.action_label)
  const colorClass = normalized ? (actionColors[normalized] || 'bg-slate-500 text-white') : 'bg-slate-500 text-white'
  const displayLabel = normalized ? (actionLabels[normalized] || suggestion.action_label) : (suggestion.action_label || '观望')
  const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技术指标'

  // 持仓页模式：小徽章 + 点击弹窗
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <div className="inline-flex items-center gap-1">
        <button
          onClick={(e) => {
            e.stopPropagation()
            if (suggestion.agent_label === '技术指标') setKlineDialogOpen(true)
            else setDialogOpen(true)
          }}
          className={`relative text-[12px] px-2.5 py-1 rounded font-medium cursor-pointer hover:opacity-80 transition-opacity whitespace-nowrap ${colorClass} ${suggestion.is_expired ? 'opacity-50' : ''}`}
          title={sourceInfo ? `${sourceInfo} - 点击查看详情` : '点击查看建议详情'}
        >
          {displayLabel}
          {isAI && (
            <span className="pointer-events-none absolute top-0 left-0 transform -translate-x-1/2 -translate-y-1/2 text-[10px] leading-none px-1.5 py-[2px] rounded-sm bg-primary text-white uppercase shadow-sm ring-1 ring-black/20">
              AI
            </span>
          )}
        </button>
        {suggestion.agent_label !== '技术指标' && (
          (() => {
            const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
            const techColor = tech ? (actionColors[tech.action] || 'bg-slate-500 text-white') : 'bg-slate-500 text-white'
            const label = tech ? tech.action_label : '观望'
            return (
              <button
                onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                className={`text-[12px] px-2.5 py-1 rounded font-medium cursor-pointer hover:opacity-80 transition-opacity ${techColor}`}
                title="点击查看技术面详情"
              >
                {label}
              </button>
            )
          })()
        )}
        </div>
        {/* 来源和时间（显示在徽章下方，仅 AI 建议以增强区分）*/}
        {isAI && (
          <div className="mt-1 text-[10px] text-muted-foreground/70">
            来源: {suggestion.agent_label || 'AI'}{timeStr && ` · ${timeStr}`}
            {suggestion.is_expired && <span className="ml-1 text-amber-600">(已过期)</span>}
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span className={`text-[12px] px-2 py-1 rounded font-medium ${colorClass}`}>
                {suggestion.action_label}
              </span>
              {stockName && (
                <span className="text-[14px] font-normal text-muted-foreground">
                  {stockName} {stockSymbol && `(${stockSymbol})`}
                </span>
              )}
            </DialogTitle>
            {/* 来源信息 */}
            {(suggestion.agent_label || suggestion.created_at) && (
              <div className="text-[11px] text-muted-foreground/70 mt-1">
                来源: {suggestion.agent_label || '未知'}
                {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                {suggestion.is_expired && <span className="ml-2 text-amber-500">(已过期)</span>}
              </div>
            )}
          </DialogHeader>

          <div className="space-y-4">
            {/* 信号 */}
            {suggestion.signal && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
              </div>
            )}

            {/* 理由 */}
            {(suggestion.reason || suggestion.raw) && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                <p className="text-[13px] text-foreground">
                  {suggestion.reason || suggestion.raw}
                </p>
              </div>
            )}

            {/* 技术指标 */}
            {kline && (
              <div className="space-y-3">
                <div className="text-[11px] text-muted-foreground">技术指标</div>
                <KlineIndicators summary={kline as any} />
              </div>
            )}

            {/* AI 原始响应 */}
            {suggestion.ai_response && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">AI 响应</div>
                <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto">
                  {suggestion.ai_response}
                </div>
              </div>
            )}

            {/* Prompt 上下文 */}
            {suggestion.prompt_context && (
              <details className="group">
                <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                  Prompt 上下文 <span className="text-[10px]">(点击展开)</span>
                </summary>
                <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto">
                  {suggestion.prompt_context}
                </div>
              </details>
            )}
          </div>
        </DialogContent>
      </Dialog>
      {/* Always mount K-line dialog for technical details */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={stockSymbol || ''}
        market={market}
        stockName={stockName}
        hasPosition={hasPosition}
        initialSummary={kline as any}
      />
    </>
  )
}
