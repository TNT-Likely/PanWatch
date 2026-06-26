/**
 * 深度分析弹窗(TradingAgents)。
 *
 * 三种状态:
 * 1. 触发中 — 显示「分析需 3-5 分钟,确认开始?」+ 成本预估
 * 2. 运行中 — polling /agents/runs/{trace_id}/progress,显示阶段进度
 * 3. 完成 — 顶层摘要 + Markdown 推理 + 可展开 4 分析师报告 + 辩论
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import {
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisMode,
  type DeepAnalysisResult,
  analystTypesForMode,
  deepAnalysisModeEta,
  loadDeepAnalysisMode,
  type ProgressResponse,
  type ProgressStage,
} from '@panwatch/api'
import { DeepAnalysisModePicker } from './deep-analysis-mode-picker'

const STAGE_LABEL: Record<string, string> = {
  market_analyst: '技术分析师',
  social_analyst: '情绪分析师',
  news_analyst: '新闻分析师',
  fundamentals_analyst: '基本面分析师',
  bull_bear_debate: '看多看空辩论',
  research_manager: '研究主管',
  trader: '交易员决策',
  risk_judge: '风控判定',
  final_decision: 'PM 整合',
}

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const POLL_INTERVAL_MS = 2000

/** localStorage 里记录某只股票最近一次触发的 trace_id;关闭重开弹窗时恢复 polling */
const STORAGE_KEY_PREFIX = 'panwatch:tradingagents:running:'
/** trace_id 持续多久后认为可能已不再运行(避免显示过期 trace 的 idle) */
const TRACE_MAX_AGE_MS = 20 * 60 * 1000  // 20 分钟

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* 忽略 quota 等错误 */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** 历史分析(若有,直接展示) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const [analysisMode, setAnalysisMode] = useState<DeepAnalysisMode>(() => loadDeepAnalysisMode())
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // trigger 时间戳:前 60s 内允许 not_found(后端日志还没来得及写),不重置
  const triggerStartedRef = useRef<number>(0)
  const NOT_FOUND_GRACE_MS = 60_000

  // 弹窗关闭时清理 polling
  useEffect(() => {
    if (!open) {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [open])

  // 重置初始状态 + 后端查询是否有正在跑/已完成的任务
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // 先重置为 idle (避免上次 state 残留),然后异步查后端
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // 并发查 3 个数据:
    //   - findRunning:这只股票最近 30 分钟有没有运行中的任务
    //   - getLatestForStock:有没有当日已完成的结果(过 30 分钟也算)
    //   - getBudget:本月预算(idle 状态展示)
    // 优先级:running > done(已有结果)> idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget(analystTypesForMode(analysisMode)).catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // 优先级:running(真在跑) > done(当日缓存,允许重新分析) > idle
      //   - stale / failed / success / none 都视为"不在跑"
      //   - 任何状态下,只要有当日缓存就展示 DoneView(含「忽略缓存重新分析」按钮)
      //   - 任何状态下,IdleView 的「开始分析」按钮永远可用,后端会做幂等去重

      // 1) 真正在跑(后端权威源)→ 进入 running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        // 后端确认在跑 → grace period 已过,不再保护 not_found
        triggerStartedRef.current = Date.now() - NOT_FOUND_GRACE_MS - 1
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        if (timerRef.current) clearInterval(timerRef.current)
        timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
        return
      }

      // 2) 后端 stale/failed → 老任务死掉/失败,清掉本地痕迹,继续走缓存判断
      //    不再回到 running,允许用户重新触发
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
        clearRunningTrace(stockSymbol)
      }

      // 3) localStorage 兜底(刚触发后端还没写 log)— 仅在后端 'none' 时尝试
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          triggerStartedRef.current = Date.now()
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          if (timerRef.current) clearInterval(timerRef.current)
          timerRef.current = setInterval(() => pollProgress(localTrace), POLL_INTERVAL_MS)
          return
        }
      }

      // 4) 有当日已完成结果 → done 视图(用户可点「忽略缓存重新分析」)
      if (latestResult) {
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 5) 都没有 → idle(开始分析按钮可用,后端幂等保护)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol, analysisMode])

  useEffect(() => {
    if (!open || stage !== 'idle' || initialResult) return
    tradingAgentsApi.getBudget(analystTypesForMode(analysisMode)).then(setBudget).catch(() => null)
  }, [analysisMode, initialResult, open, stage])

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        setProgress(resp)
        if (resp.status === 'success' && resp.run) {
          // 完成,拉历史结果
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          clearRunningTrace(stockSymbol)
          const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
          if (latest) {
            setResult(latest)
            setStage('done')
          } else {
            setError('结果未落库,请稍后到「AI 历史」查看')
            setStage('error')
          }
        } else if (resp.status === 'failed') {
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          clearRunningTrace(stockSymbol)
          setError(resp.run?.error || '分析失败')
          setStage('error')
        } else if (resp.status === 'stale') {
          // 后端检测到僵尸 running(5 分钟无新进度,server 重启 / 进程死掉)
          // → 自动重置到 idle,用户可以重新触发
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          clearRunningTrace(stockSymbol)
          setTraceId('')
          setProgress(null)
          setStage('idle')
        } else if (resp.status === 'not_found') {
          // trigger 刚发出时后端可能还没写日志,前 60s 视为正常等待,
          // 超过 grace 仍 not_found → 视作触发失败,reset 到 idle
          const sinceTrigger = Date.now() - triggerStartedRef.current
          if (triggerStartedRef.current > 0 && sinceTrigger > NOT_FOUND_GRACE_MS) {
            if (timerRef.current) {
              clearInterval(timerRef.current)
              timerRef.current = null
            }
            clearRunningTrace(stockSymbol)
            setTraceId('')
            setProgress(null)
            setStage('idle')
          }
        }
      } catch (e) {
        // polling 失败不立即终止,记一次错误
        console.warn('progress poll error:', e)
      }
    },
    [stockSymbol],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    triggerStartedRef.current = Date.now()
    const analystTypes = analystTypesForMode(analysisMode)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force, analystTypes })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // 后端未返回 trace_id,只显示 message
        setStage('done')
        toast(triggerResp.message || '已触发', 'success')
        return
      }
      // 持久化 trace_id 让关闭重开能恢复进度
      saveRunningTrace(stockSymbol, tid)
      // 启动 polling
      timerRef.current = setInterval(() => {
        pollProgress(tid)
      }, POLL_INTERVAL_MS)
      // 立即拉一次
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : '触发失败')
    }
  }, [analysisMode, stockId, stockSymbol, pollProgress, toast])

  const handleClose = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    onOpenChange(false)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 深度分析 · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            TradingAgents 多 Agent 决策框架 · 仅供学习研究参考,不构成投资建议
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            analysisMode={analysisMode}
            onAnalysisModeChange={setAnalysisMode}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={stockSymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">分析失败</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>关闭</Button>
              <Button onClick={() => handleStart(false)}>重试</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  analysisMode,
  onAnalysisModeChange,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  analysisMode: DeepAnalysisMode
  onAnalysisModeChange: (mode: DeepAnalysisMode) => void
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">即将分析:{stockSymbol}</div>
        <DeepAnalysisModePicker mode={analysisMode} onChange={onAnalysisModeChange} />
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ 预计耗时:{deepAnalysisModeEta(analysisMode)}</div>
          {est ? (
            <div>💰 预估成本:${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 预估成本:加载中...</div>
          )}
          <div>ℹ️ 异步执行,可关闭弹窗,完成时通过通知渠道推送</div>
        </div>
      </div>

      {/* 本月预算 */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">本月预算</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} 次`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ 本月预算已用尽。如需继续,请到「设置 → Agent → TradingAgents」调高 `monthly_budget_usd`。
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>取消</Button>
        <Button onClick={onStart} disabled={overBudget}>开始分析</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">分析进行中...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            已用 {formatElapsed(elapsed)} · ${cost.toFixed(4)}
          </span>
        </div>
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">准备中...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>

      <ToolkitDiagnostics
        summary={progress?.toolkit_summary}
        recent={progress?.toolkit_recent || []}
      />

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          后台运行 (完成时推送通知)
        </Button>
      </div>
    </div>
  )
}

interface ToolkitDiagItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface ToolkitDiagSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}

export function ToolkitDiagnostics({
  summary,
  recent,
  defaultOpen = false,
}: {
  summary: ToolkitDiagSummary | undefined
  recent: ToolkitDiagItem[]
  defaultOpen?: boolean
}) {
  if (!summary && recent.length === 0) return null

  const hit = summary?.hit ?? 0
  const miss = summary?.miss ?? 0
  const pass = summary?.passthrough ?? 0
  const fall = summary?.fallthrough ?? 0
  const err = summary?.error ?? 0
  const total = hit + miss + pass + fall + err

  const ACTION_CLS: Record<string, string> = {
    HIT: 'text-emerald-600 dark:text-emerald-400',
    MISS: 'text-amber-600 dark:text-amber-400',
    PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
    FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
    ERROR: 'text-rose-600',
  }

  return (
    <details className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]" open={defaultOpen}>
      <summary className="cursor-pointer flex items-center gap-2 flex-wrap">
        <span className="font-medium">数据注入诊断</span>
        <span className="text-[11px] text-muted-foreground">
          (AlphaMind 数据 → TradingAgents 工具)
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · 透传 {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · 兜底 {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · 错误 {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: 用 AlphaMind 数据 ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: 命中但 AlphaMind 未实现 ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>透传</span>: 非 A 股直接走上游 vendor ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>兜底</span>: A 股但 cache 为空,走了上游
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          ⚠️ 还没有任何工具调用记录(可能 TradingAgents 还在准备阶段)。
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {recent.map((h, i) => {
            const action = (h.action || '').toUpperCase()
            const row = (
              <div className="font-mono text-[10.5px] flex items-center gap-2 hover:bg-accent/30 px-1 rounded cursor-help w-full">
                <span className={`${ACTION_CLS[action] || 'text-muted-foreground'} w-20 shrink-0`}>
                  {action}
                </span>
                <span className="text-foreground/80 truncate flex-1 text-left">
                  {h.method} ({h.symbol || '-'})
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                  {h.chars != null && <span className="text-muted-foreground"> · {h.chars} 字符</span>}
                  {h.source && <span className="text-muted-foreground/70"> · {h.source}</span>}
                </span>
              </div>
            )
            const hasDetail = !!(h.snippet || h.reason)
            if (!hasDetail) return <div key={i}>{row}</div>
            return (
              <HoverPopover
                key={i}
                className="block w-full"
                trigger={row}
                title={
                  <span>
                    <span className={ACTION_CLS[action] || 'text-muted-foreground'}>{action}</span>
                    <span className="text-muted-foreground"> · {h.method}({h.symbol || '-'})</span>
                    {h.source && (
                      <span className="text-muted-foreground/70"> · {h.source}</span>
                    )}
                  </span>
                }
                content={
                  <div className="space-y-2">
                    {h.reason && (
                      <div className="text-[11px] text-amber-600 dark:text-amber-400">
                        {h.reason}
                      </div>
                    )}
                    {h.snippet && (
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}...(共 {h.chars} 字符,仅展示前 {h.snippet.length})
                          </span>
                        )}
                      </pre>
                    )}
                  </div>
                }
                popoverClassName="w-[44rem] max-w-[90vw]"
                side="top"
                align="start"
              />
            )
          })}
        </div>
      )}
    </details>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-primary'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  stockSymbol,
  onRerun,
}: {
  result: DeepAnalysisResult
  stockSymbol: string
  onRerun: () => void
}) {
  // 防御性默认值:后端拉历史时可能 raw_data 缺失,这里给完整 fallback 避免白屏
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: '持有',
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: 'TradingAgents 深度',
    confidence: 5.0,
  }
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd
  const sections = buildAnalysisSections(rawData)
  const analysisDate = result.timestamp
    ? String(result.timestamp).slice(0, 10)
    : new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ 当日缓存:今天已经分析过这只股票,展示缓存结果(无新成本)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            忽略缓存重新分析
          </Button>
        </div>
      )}

      {/* 顶层摘要(精简成一行:决策 + 置信度 + 成本;完整理由在"最终决策" tab) */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        <span className={`text-[18px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
          {sug.action_label}
        </span>
        <span className="text-[12px] text-muted-foreground">
          置信度 {sug.confidence?.toFixed(1) ?? '-'} / 10
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          onClick={() => window.open(`/analysis/${stockSymbol}/${analysisDate}`, '_blank')}
        >
          查看详细页
        </Button>
        <span className="text-[10px] text-muted-foreground">
          成本:${costUsd?.toFixed(4) ?? '-'}
        </span>
      </div>

      {/* 统一 tab:最终决策 + 四位分析师 + 看多看空辩论 + 风控辩论(完整 + GFM 表格) */}
      <AnalysisTabs sections={sections} />

      {/* 数据注入诊断(历史报告):从 raw_data.toolkit_diagnostic 拿 */}
      {rawData.toolkit_diagnostic && (
        <ToolkitDiagnostics
          summary={rawData.toolkit_diagnostic.summary}
          recent={rawData.toolkit_diagnostic.recent || []}
        />
      )}

      {/* 免责声明 */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,仅供学习研究参考,不构成任何投资建议。
        投资有风险,决策需自主判断。
      </div>
    </div>
  )
}

/** 决策与分析统一 tab。内容由 buildAnalysisSections 组装(弹窗与详细页共用),只渲染有内容的 tab。 */
function AnalysisTabs({ sections }: { sections: AnalysisSection[] }) {
  if (sections.length === 0) return null
  return (
    <div className="rounded-lg border border-border/50 p-4">
      <Tabs defaultValue={sections[0].id}>
        <TabsList>
          {sections.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {sections.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-table:my-3 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-table:text-[12px] prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.markdown}</ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
