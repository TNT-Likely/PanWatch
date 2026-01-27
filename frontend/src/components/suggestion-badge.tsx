import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

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
}

export interface KlineSummary {
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

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)

  if (!suggestion) return null

  const colorClass = actionColors[suggestion.action] || 'bg-slate-500 text-white'

  // Dashboard 模式：行内显示完整信息
  if (showFullInline) {
    const timeStr = formatSuggestionTime(suggestion.created_at)
    return (
      <div className="pt-3 border-t border-border/30">
        <div className="flex items-start gap-3">
          <div className="shrink-0">
            <span className={`text-[11px] px-2 py-1 rounded font-medium ${colorClass}`}>
              {suggestion.action_label}
            </span>
            {/* 来源和时间 */}
            {(suggestion.agent_label || timeStr) && (
              <div className="text-[10px] text-muted-foreground/70 mt-1 text-center">
                {suggestion.agent_label}{suggestion.agent_label && timeStr && ' · '}{timeStr}
              </div>
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
          </div>
        </div>
      </div>
    )
  }

  // 持仓页模式：小徽章 + 点击弹窗
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = suggestion.agent_label
    ? `${suggestion.agent_label}${timeStr ? ` · ${timeStr}` : ''}`
    : ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <button
          onClick={(e) => {
            e.stopPropagation()
            setDialogOpen(true)
          }}
          className={`text-[10px] px-1.5 py-0.5 rounded font-medium cursor-pointer hover:opacity-80 transition-opacity ${colorClass} ${suggestion.is_expired ? 'opacity-50' : ''}`}
          title={sourceInfo ? `${sourceInfo} - 点击查看详情` : '点击查看 AI 建议详情'}
        >
          {suggestion.action_label}
        </button>
        {/* 来源和时间（显示在徽章下方） */}
        {sourceInfo && (
          <span className="text-[9px] text-muted-foreground/60">
            {sourceInfo}
          </span>
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

                {/* 趋势与形态 */}
                <div className="flex flex-wrap gap-2 text-[11px]">
                  <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">
                    {kline.trend}
                  </span>
                  <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">
                    MACD {kline.macd_status}
                  </span>
                  {kline.rsi_status && (
                    <span className={`px-2 py-0.5 rounded ${
                      kline.rsi_status === '超买' ? 'bg-rose-500/10 text-rose-600' :
                      kline.rsi_status === '超卖' ? 'bg-emerald-500/10 text-emerald-600' :
                      'bg-accent/50 text-muted-foreground'
                    }`}>
                      RSI {kline.rsi_status}{kline.rsi6 != null && ` (${kline.rsi6.toFixed(0)})`}
                    </span>
                  )}
                  {kline.kdj_status && (
                    <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">
                      KDJ {kline.kdj_status}
                    </span>
                  )}
                  {kline.volume_trend && (
                    <span className={`px-2 py-0.5 rounded ${
                      kline.volume_trend === '放量' ? 'bg-amber-500/10 text-amber-600' :
                      kline.volume_trend === '缩量' ? 'bg-blue-500/10 text-blue-600' :
                      'bg-accent/50 text-muted-foreground'
                    }`}>
                      {kline.volume_trend}{kline.volume_ratio != null && ` (${kline.volume_ratio.toFixed(1)}x)`}
                    </span>
                  )}
                  {kline.boll_status && (
                    <span className={`px-2 py-0.5 rounded ${
                      kline.boll_status === '突破上轨' ? 'bg-rose-500/10 text-rose-600' :
                      kline.boll_status === '跌破下轨' ? 'bg-emerald-500/10 text-emerald-600' :
                      'bg-accent/50 text-muted-foreground'
                    }`}>
                      布林 {kline.boll_status}
                    </span>
                  )}
                  {kline.kline_pattern && (
                    <span className={`px-2 py-0.5 rounded ${
                      kline.kline_pattern.includes('阳') || kline.kline_pattern.includes('涨') ? 'bg-rose-500/10 text-rose-600' :
                      kline.kline_pattern.includes('阴') || kline.kline_pattern.includes('跌') ? 'bg-emerald-500/10 text-emerald-600' :
                      'bg-amber-500/10 text-amber-600'
                    }`}>
                      {kline.kline_pattern}
                    </span>
                  )}
                </div>

                {/* 支撑压力 */}
                <div className="flex flex-wrap gap-2 text-[11px]">
                  {kline.support && (
                    <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600">
                      支撑 {kline.support.toFixed(2)}
                    </span>
                  )}
                  {kline.resistance && (
                    <span className="px-2 py-0.5 rounded bg-rose-500/10 text-rose-600">
                      压力 {kline.resistance.toFixed(2)}
                    </span>
                  )}
                </div>

                {/* 涨跌幅 */}
                {(kline.change_5d !== null || kline.change_20d !== null) && (
                  <div className="flex gap-4 text-[11px] text-muted-foreground">
                    {kline.change_5d !== null && (
                      <span>5日: <span className={kline.change_5d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {kline.change_5d >= 0 ? '+' : ''}{kline.change_5d.toFixed(2)}%
                      </span></span>
                    )}
                    {kline.change_20d !== null && (
                      <span>20日: <span className={kline.change_20d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {kline.change_20d >= 0 ? '+' : ''}{kline.change_20d.toFixed(2)}%
                      </span></span>
                    )}
                    {kline.amplitude != null && (
                      <span>振幅: {kline.amplitude.toFixed(2)}%</span>
                    )}
                  </div>
                )}
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
    </>
  )
}
