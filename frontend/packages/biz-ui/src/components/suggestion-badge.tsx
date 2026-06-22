import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { fetchAPI } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { AiSuggestionBadge } from '@panwatch/biz-ui/components/ai-suggestion-badge'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface SuggestionInfo {
  id?: number
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
  showTechnicalCompanion?: boolean // 是否展示技术指标对照徽章
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

const MARKDOWN_PROSE =
  'prose prose-sm dark:prose-invert max-w-none break-words text-[13px] leading-relaxed [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5 [&_strong]:text-foreground'

function markdownToPlainText(input?: string): string {
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

function extractExecutiveSummary(text?: string): string {
  const raw = String(text || '').trim()
  if (!raw) return ''
  const m = raw.match(
    /(?:Executive\s+Summary|决策摘要|核心结论|执行摘要)[\s*:：\-—－]+(.+?)(?=\*\*[A-Za-z\u4e00-\u9fff]|$)/is,
  )
  if (!m?.[1]) return ''
  return m[1].replace(/\*\*/g, '').trim()
}

/** TradingAgents 旧数据 signal 取自交易员计划,可能与 PM 最终决策矛盾;优先用 reason 里的摘要。 */
function resolveDisplaySignal(suggestion: SuggestionInfo): string {
  if (suggestion.agent_name === 'tradingagents') {
    const fromReason = extractExecutiveSummary(suggestion.reason)
    if (fromReason) return fromReason
  }
  return suggestion.signal || ''
}

function SuggestionMarkdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={`${MARKDOWN_PROSE} ${className || ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false,
  market = 'CN',
  hasPosition = false,
  showTechnicalCompanion = true,
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [feedback, setFeedback] = useState<'useful' | 'useless' | null>(null)
  const { toast } = useToast()

  useEffect(() => {
    setFeedback(null)
  }, [suggestion?.id])

  const canFeedback = !!suggestion?.id && suggestion?.agent_label !== '技术指标'
  const submitFeedback = async (useful: boolean) => {
    if (!suggestion?.id) return
    try {
      await fetchAPI('/feedback', {
        method: 'POST',
        body: JSON.stringify({ suggestion_id: suggestion.id, useful }),
      })
      setFeedback(useful ? 'useful' : 'useless')
      toast('反馈已提交', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '反馈失败', 'error')
    }
  }

  const onDialogOpenChange = (open: boolean) => {
    setDialogOpen(open)
    if (!open) {
      try {
        ;(window as any).__panwatch_suppress_card_click_until = Date.now() + 600
      } catch {
        // ignore
      }
    }
  }

  if (!suggestion && !kline) return null

  // Dashboard 模式：行内显示完整信息（仅建议 badge）
  if (showFullInline) {
    if (!suggestion) return null
    const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技术指标'
    const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
    const timeStr = formatSuggestionTime(suggestion.created_at)
    const klineMetaStr = formatKlineMeta(suggestion.meta)
    const displaySignal = resolveDisplaySignal(suggestion)
    const displayReason = suggestion.reason || suggestion.raw || ''
    return (
      <>
        <div className="pt-3 border-t border-border/30">
          <div className="flex items-start gap-3">
            <div className="shrink-0 flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="lg"
                onClick={(e) => {
                  e.stopPropagation()
                  if (suggestion.agent_label === '技术指标') setKlineDialogOpen(true)
                  else setDialogOpen(true)
                }}
                title="点击查看建议详情"
              />
              {isAI && showTechnicalCompanion && (
                <TechnicalBadge
                  label={tech ? tech.action_label : '观望'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="lg"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="点击查看技术面详情"
                />
              )}
            </div>
            <div className="flex-1 min-w-0">
              {displaySignal && (
                <p className="text-[12px] font-medium text-foreground mb-0.5 line-clamp-2">
                  {markdownToPlainText(displaySignal)}
                </p>
              )}
              {displayReason ? (
                <p className="text-[11px] text-muted-foreground line-clamp-2">
                  {markdownToPlainText(displayReason)}
                </p>
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

        <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
          <DialogContent
            className="max-w-md"
            onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AiSuggestionBadge
                  action={suggestion.action}
                  actionLabel={suggestion.action_label}
                  isAI={isAI}
                  isExpired={!!suggestion.is_expired}
                  size="lg"
                />
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
              {/* Feedback */}
              {canFeedback && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">这条建议是否有用？</div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => submitFeedback(true)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useful'
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      有用
                    </button>
                    <button
                      onClick={() => submitFeedback(false)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useless'
                          ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      没用
                    </button>
                    {feedback && (
                      <span className="text-[11px] text-muted-foreground">已记录，感谢反馈</span>
                    )}
                  </div>
                </div>
              )}

              {/* 信号 */}
              {displaySignal && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                  <SuggestionMarkdown content={displaySignal} className="font-medium" />
                </div>
              )}

              {/* 理由 */}
              {displayReason && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                  <SuggestionMarkdown content={displayReason} />
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
                  <div className="bg-accent/30 rounded p-2 max-h-48 overflow-y-auto scrollbar">
                    <SuggestionMarkdown content={suggestion.ai_response} className="text-[12px]" />
                  </div>
                </div>
              )}

              {/* Prompt 上下文 */}
              {suggestion.prompt_context && (
                <details className="group">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                    Prompt 上下文 <span className="text-[10px]">(点击展开)</span>
                  </summary>
                  <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto scrollbar">
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
          <TechnicalBadge
            label="指标"
            tone="neutral"
            size="xs"
            onClick={(e) => {
              e.stopPropagation()
              setKlineDialogOpen(true)
            }}
            title="点击查看技术指标"
          />
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
  const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技术指标'
  const displaySignal = resolveDisplaySignal(suggestion)
  const displayReason = suggestion.reason || suggestion.raw || ''

  // 持仓页模式：小徽章 + 点击弹窗
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <div className="inline-flex items-center gap-1">
          <AiSuggestionBadge
            action={suggestion.action}
            actionLabel={suggestion.action_label}
            isAI={isAI}
            isExpired={!!suggestion.is_expired}
            size="md"
            onClick={(e) => {
              e.stopPropagation()
              if (suggestion.agent_label === '技术指标') setKlineDialogOpen(true)
              else setDialogOpen(true)
            }}
            title={sourceInfo ? `${sourceInfo} - 点击查看详情` : '点击查看建议详情'}
          />
          {showTechnicalCompanion && suggestion.agent_label !== '技术指标' && (
            (() => {
              const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
              return (
                <TechnicalBadge
                  label={tech ? tech.action_label : '观望'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="md"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="点击查看技术面详情"
                />
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

      <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
        <DialogContent
          className="max-w-md"
          onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onClick={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="md"
              />
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
            {/* Feedback */}
            {canFeedback && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">这条建议是否有用？</div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => submitFeedback(true)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useful'
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    有用
                  </button>
                  <button
                    onClick={() => submitFeedback(false)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useless'
                        ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    没用
                  </button>
                  {feedback && (
                    <span className="text-[11px] text-muted-foreground">已记录，感谢反馈</span>
                  )}
                </div>
              </div>
            )}

            {/* 信号 */}
            {displaySignal && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                <SuggestionMarkdown content={displaySignal} className="font-medium" />
              </div>
            )}

            {/* 理由 */}
            {displayReason && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                <SuggestionMarkdown content={displayReason} />
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
                <div className="bg-accent/30 rounded p-2 max-h-48 overflow-y-auto scrollbar">
                  <SuggestionMarkdown content={suggestion.ai_response} className="text-[12px]" />
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
