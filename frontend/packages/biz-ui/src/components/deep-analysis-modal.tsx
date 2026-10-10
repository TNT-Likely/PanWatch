/**
 * 深度分析弹窗(TradingAgents)。
 *
 * 三种状态:
 * 1. 触发中 — 显示「分析需 3-5 分钟,确认开始?」
 * 2. 运行中 — polling /agents/runs/{trace_id}/progress,显示阶段进度
 * 3. 完成 — 顶层摘要 + Markdown 推理 + 可展开 4 分析师报告 + 辩论
 */
import { useEffect, useLayoutEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { AnalysisMetadata, AnalysisUsage, analysisDateForResult } from './analysis-metadata'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import {
  subscribeSSE,
  tradingAgentsApi,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressDataSource,
  type ProgressStage,
} from '@panwatch/api'
import {
  isTerminalProgressStatus,
  shouldContinueProgressWatch,
} from '../../../../src/lib/tradingagents-progress'
import { useTranslation } from 'react-i18next'
import { suggestionPresentation } from './suggestion-action'
import { useRequestScope } from './use-request-scope'

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-market-up',
  add: 'text-market-up',
  hold: 'text-amber-600 dark:text-amber-400',
  reduce: 'text-market-down',
  sell: 'text-market-down',
}

const POLL_INTERVAL_MS = 2000

/** localStorage 里记录某只股票最近一次触发的 trace_id;关闭重开弹窗时恢复 polling */
const STORAGE_KEY_PREFIX = 'panwatch:tradingagents:running:'
/** trace_id 持续多久后认为可能已不再运行(避免显示过期 trace 的 idle) */
const TRACE_MAX_AGE_MS = 60 * 60 * 1000  // 与后端 running 生命周期窗口保持一致并留出恢复余量

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
  stockMarket?: string
  /** 历史分析(若有,直接展示) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal(props: DeepAnalysisModalProps) {
  if (!props.open) return null
  return <DeepAnalysisSession key={JSON.stringify([props.stockMarket, props.stockSymbol, props.stockId])} {...props} />
}

function DeepAnalysisSession({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  stockMarket,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const beginRequest = useRequestScope()
  const historySymbol = stockMarket === 'TSX' || stockMarket === 'TSXV' ? `${stockMarket}:${stockSymbol}` : stockSymbol
  const { toast } = useToast()
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>(initialResult ? 'done' : 'idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const watcherRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // SSE 订阅取消函数(进度优先走 SSE,失败降级 polling)
  const sseCloseRef = useRef<(() => void) | null>(null)

  /** 停止一切进度监听(SSE + polling) */
  const stopWatching = useCallback(() => {
    watcherRef.current += 1
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (sseCloseRef.current) {
      sseCloseRef.current()
      sseCloseRef.current = null
    }
  }, [])

  // Close only the view's watchers. The submitted analysis keeps running and
  // can be discovered again through its qualified symbol when the view reopens.
  useLayoutEffect(() => stopWatching, [stopWatching])

  const handleProgressResponse = useCallback(async (resp: ProgressResponse, isCurrent: () => boolean) => {
    if (!isCurrent()) return
    setProgress(resp)
    if (resp.status === 'success') {
      stopWatching()
      clearRunningTrace(historySymbol)
      try {
        const latest = await tradingAgentsApi.getLatestForStock(historySymbol)
        if (!isCurrent()) return
        if (latest) {
          setResult(latest)
          setStage('done')
        } else {
          setError(tr('errors.resultMissing'))
          setStage('error')
        }
      } catch (e) {
        if (!isCurrent()) return
        setError(e instanceof Error ? e.message : tr('errors.resultMissing'))
        setStage('error')
      }
    } else if (resp.status === 'failed') {
      stopWatching()
      clearRunningTrace(historySymbol)
      setError(resp.run?.error || tr('errors.analysisFailed'))
      setStage('error')
    } else if (resp.status === 'stale') {
      stopWatching()
      clearRunningTrace(historySymbol)
      setTraceId(null)
      setProgress(null)
      setStage('idle')
    }
  }, [historySymbol, stopWatching])

  const startWatching = useCallback((tid: string, isCurrent: () => boolean) => {
    if (!isCurrent()) return
    stopWatching()
    const watcher = watcherRef.current
    const isWatching = () => isCurrent() && watcherRef.current === watcher
    let terminal = false
    let polling = false
    const poll = async () => {
      // Coalesce timer ticks: a slow response must not be superseded forever by
      // the next interval, while newer SSE snapshots still invalidate it.
      if (!isWatching() || polling) return
      polling = true
      const isLatest = beginRequest('progress')
      try {
        const response = await tradingAgentsApi.getProgress(tid)
        if (isWatching() && isLatest()) await handleProgressResponse(response, isCurrent)
      } catch {
        // A temporary transport error does not cancel the backend analysis.
      } finally {
        polling = false
      }
    }
    const startPolling = () => {
      if (!isWatching() || terminal || timerRef.current) return
      timerRef.current = setInterval(() => { void poll() }, POLL_INTERVAL_MS)
      void poll()
    }
    const close = subscribeSSE(`/agents/runs/${tid}/progress/stream`, {
      onEvent: (ev) => {
        if (!isWatching()) return
        if (ev.event === 'progress' && ev.data && typeof ev.data === 'object') {
          // An SSE snapshot is newer than any still-pending polling response.
          beginRequest('progress')
          const response = ev.data as ProgressResponse
          if (isTerminalProgressStatus(response.status)) terminal = true
          void handleProgressResponse(response, isCurrent)
        } else if (ev.event === 'done' && ev.data?.status && ev.data.status !== 'timeout') {
          // A done hint has no complete snapshot. Keep fallback available if
          // this final fetch fails or the saved result has not caught up yet.
          if (!shouldContinueProgressWatch(ev.data.status, 'done')) void poll()
        }
      },
      onClosed: startPolling,
      onFailed: startPolling,
    })
    // Some transports invoke callbacks synchronously while subscribing.
    if (isWatching()) sseCloseRef.current = close
    else close()
    void poll()
  }, [beginRequest, handleProgressResponse, stopWatching])

  useEffect(() => {
    const isCurrent = beginRequest('run')
    stopWatching()
    setTraceId(null)
    setProgress(null)
    setError('')
    setResult(initialResult)
    setStage(initialResult ? 'done' : 'idle')
    if (initialResult) return
    void (async () => {
      const [runningInfo, latestResult] = await Promise.all([
        tradingAgentsApi.findRunning(historySymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
        tradingAgentsApi.getLatestForStock(historySymbol).catch(() => null),
      ])
      if (!isCurrent()) return
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        setTraceId(runningInfo.trace_id)
        setStage('running')
        startWatching(runningInfo.trace_id, isCurrent)
        return
      }
      if (runningInfo.status === 'failed' && runningInfo.trace_id) {
        const failed = await tradingAgentsApi.getProgress(runningInfo.trace_id).catch(() => null)
        if (!isCurrent()) return
        if (failed?.status === 'failed') {
          setTraceId(runningInfo.trace_id)
          setProgress(failed)
          setError(failed.run?.error || tr('errors.analysisFailed'))
          setStage('error')
          clearRunningTrace(historySymbol)
          return
        }
      }
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') clearRunningTrace(historySymbol)
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(historySymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          startWatching(localTrace, isCurrent)
          return
        }
      }
      if (latestResult) {
        setResult({ ...latestResult, raw_data: { ...latestResult.raw_data, from_cache: true } })
        setStage('done')
      }
      clearRunningTrace(historySymbol)
    })()
  }, [initialResult, historySymbol, beginRequest, startWatching, stopWatching])

  const handleStart = useCallback(async (force = false) => {
    // Invalidate both initial restoration and any prior run before triggering.
    const isCurrent = beginRequest('run')
    if (!isCurrent()) return
    stopWatching()
    setStage('running')
    setError('')
    setProgress(null)
    setResult(null)
    setTraceId(null)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      // A closed view must not overwrite a newer session's recovery trace.
      // The backend can still discover the submitted job when reopened.
      if (!isCurrent()) return
      if (tid) saveRunningTrace(historySymbol, tid)
      setTraceId(tid)
      if (!tid) {
        setStage('done')
        toast(triggerResp.message || tr('triggered'), 'success')
        return
      }
      startWatching(tid, isCurrent)
    } catch (e) {
      if (!isCurrent()) return
      setStage('error')
      setError(e instanceof Error ? e.message : tr('errors.triggerFailed'))
    }
  }, [stockId, historySymbol, startWatching, stopWatching, toast, beginRequest])

  const handleClose = useCallback(() => {
    beginRequest('run')
    stopWatching()
    onOpenChange(false)
  }, [onOpenChange, stopWatching, beginRequest])

  return (
    <Dialog open={open} onOpenChange={nextOpen => nextOpen ? onOpenChange(true) : handleClose()}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {tr('title', { name: stockName, symbol: stockSymbol })}
          </DialogTitle>
          <DialogDescription>
            {tr('description')}
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={historySymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">{tr('failedTitle')}</div>
              <div className="text-[12px] whitespace-pre-wrap break-words">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>{tr('close')}</Button>
              <Button onClick={() => handleStart(false)}>{tr('retry')}</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  onStart: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">{tr('idle.upcoming', { symbol: stockSymbol })}</div>
        <div className="text-muted-foreground">
          {tr('idle.framework')}
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>{tr('idle.duration')}</div>
          <div>{tr('idle.async')}</div>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>{tr('idle.cancel')}</Button>
        <Button onClick={onStart}>{tr('idle.start')}</Button>
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
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
  const elapsed = progress?.elapsed_sec ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">{tr('running.title')}</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {tr('running.elapsed', { time: formatElapsed(elapsed) })}
          </span>
        </div>
        <AnalysisUsage usage={progress?.token_usage} />
        {progress?.active_operation && (
          <div className="text-[11px] text-muted-foreground">
            {progress.active_operation.agent && (
              <>
                {tr('running.currentAgent')}<span className="font-mono">{progress.active_operation.agent}</span> ·{' '}
              </>
            )}
            {tr('running.currentOperation')}{progress.active_operation.kind === 'tool' ? tr('running.dataTool') : ''}
            <span className="font-mono">{progress.active_operation.name}</span>
          </div>
        )}
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">{tr('running.preparing')}</div>
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

      {progress?.data_sources && progress.data_sources.length > 0 && (
        <DataCollectionDiagnostics sources={progress.data_sources} />
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          {tr('running.background')}
        </Button>
      </div>
    </div>
  )
}

function DataCollectionDiagnostics({ sources }: { sources: ProgressDataSource[] }) {
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
  const statusClasses: Record<ProgressDataSource['status'], string> = {
    pending: 'text-muted-foreground',
    running: 'text-sky-600 dark:text-sky-400',
    done: 'text-emerald-600 dark:text-emerald-400',
    error: 'text-amber-600 dark:text-amber-400',
  }

  return (
    <div className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]">
      <div className="font-medium mb-1">{tr('data.title')}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {sources.map((source) => (
          (() => {
            const sourceKey = `data.sources.${source.name}`
            const translatedSource = tr(sourceKey)
            return (
              <span key={source.name} className={statusClasses[source.status]} title={source.error}>
                {translatedSource === `deepAnalysis.${sourceKey}` ? source.name : translatedSource}: {tr(`data.statuses.${source.status}`)}
              </span>
            )
          })()
        ))}
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
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
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
        <span className="font-medium">{tr('toolkit.title')}</span>
        <span className="text-[11px] text-muted-foreground">
          {tr('toolkit.subtitle')}
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · {tr('toolkit.passthrough')} {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · {tr('toolkit.fallback')} {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · {tr('toolkit.error')} {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: {tr('toolkit.legendHit')} ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: {tr('toolkit.legendMiss')} ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>{tr('toolkit.passthrough')}</span>: {tr('toolkit.legendPassthrough')} ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>{tr('toolkit.fallback')}</span>: {tr('toolkit.legendFallback')}
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          {tr('toolkit.empty')}
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto scrollbar">
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
                  {h.chars != null && <span className="text-muted-foreground">{tr('toolkit.chars', { count: h.chars })}</span>}
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
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto scrollbar">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}{tr('toolkit.truncated', { total: h.chars, shown: h.snippet.length })}
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
  const { t } = useTranslation('bizUi')
  const translationKey = `deepAnalysis.stages.${stage.name}`
  const translated = (t as unknown as (key: string) => string)(translationKey)
  const label = translated === translationKey ? stage.name : translated
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
  const { t, i18n } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.${key}`, options)
  const english = (i18n.resolvedLanguage || i18n.language).toLowerCase().startsWith('en')
  // 防御性默认值:后端拉历史时可能 raw_data 缺失,这里给完整 fallback 避免白屏
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: tr('done.fallbackAction'),
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: tr('done.fallbackAgent'),
    confidence: 5.0,
  }
  const view = suggestionPresentation(sug)
  const fromCache = rawData.from_cache
  const sections = buildAnalysisSections(rawData, { english })
  const analysisDate = analysisDateForResult(result)

  return (
    <div className="space-y-4 text-[13px]">
      <AnalysisMetadata result={result} />
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>{tr('done.cached')}</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            {tr('done.rerun')}
          </Button>
        </div>
      )}

      {/* 决策与置信度；报告日期和实际用量单独展示。 */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        <span className={`text-[18px] font-bold ${view.review ? 'text-orange-500' : DECISION_COLOR[view.action] || ''}`}>
          {(t as unknown as (key: string) => string)(view.labelKey)}
        </span>
        <span className="text-[12px] text-muted-foreground">
          {tr('done.confidence', { value: sug.confidence?.toFixed(1) ?? '-' })}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          disabled={!analysisDate}
          onClick={() => window.open(`/analysis/${encodeURIComponent(stockSymbol)}/${analysisDate}`, '_blank')}
        >
          {tr('done.details')}
        </Button>
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
        {tr('done.disclaimer')}
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
