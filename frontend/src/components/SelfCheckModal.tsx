import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, AlertTriangle, XCircle, RefreshCw, Loader2 } from 'lucide-react'
import { healthApi, type SelfCheckItem } from '@panwatch/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Badge } from '@panwatch/base-ui/components/ui/badge'

interface SelfCheckModalProps {
  open: boolean
  onClose: () => void
}

type RowStatus = 'checking' | SelfCheckItem['status']

interface CheckRow {
  category: string
  key: string
  name: string
  group: string | null
  status: RowStatus
  latency_ms: number
  error: string | null
  hint: string
  note: string | null
}

const CATEGORY_LABELS: Record<string, string> = {
  system: '系统',
  datasource: '数据源',
  ai: 'AI模型',
  notify: '通知渠道',
}
const CATEGORY_ORDER = ['system', 'datasource', 'ai', 'notify']
const CONCURRENCY = 4

const STATUS_META: Record<RowStatus, {
  label: string
  variant: 'success' | 'destructive' | 'outline' | 'secondary'
  className: string
  Icon: typeof CheckCircle2
}> = {
  checking: { label: '检查中', variant: 'secondary', className: 'text-muted-foreground', Icon: Loader2 },
  ok: { label: '通', variant: 'success', className: '', Icon: CheckCircle2 },
  slow: {
    label: '慢',
    variant: 'outline',
    className: 'border-amber-500/30 bg-amber-500/10 text-amber-600',
    Icon: AlertTriangle,
  },
  fail: { label: '断', variant: 'destructive', className: '', Icon: XCircle },
}

function StatusBadge({ status }: { status: RowStatus }) {
  const meta = STATUS_META[status]
  const Icon = meta.Icon
  return (
    <Badge variant={meta.variant} className={meta.className}>
      <Icon className={`w-3 h-3 ${status === 'checking' ? 'animate-spin' : ''}`} />
      {meta.label}
    </Badge>
  )
}

function ItemRow({ item }: { item: CheckRow }) {
  return (
    <div className="rounded-lg bg-background/60 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <StatusBadge status={item.status} />
          <span className="truncate text-[12px] font-medium text-foreground">{item.name}</span>
        </div>
        <span className="flex-shrink-0 font-mono text-[11px] text-muted-foreground">
          {item.status === 'checking' ? '…' : `${item.latency_ms}ms`}
        </span>
      </div>
      {item.status === 'fail' && (
        <div className="mt-1.5 space-y-1">
          {item.error && (
            <p className="truncate text-[11px] text-muted-foreground/70" title={item.error}>
              {item.error}
            </p>
          )}
          {item.hint && <p className="text-[11px] font-medium text-rose-600">{item.hint}</p>}
        </div>
      )}
      {item.status !== 'fail' && item.status !== 'checking' && item.note && (
        <p className="mt-1.5 text-[11px] text-muted-foreground/70">{item.note}</p>
      )}
    </div>
  )
}

/** 按服务商(group)分组,保留出现顺序。 */
function groupByService(rows: CheckRow[]): Array<[string, CheckRow[]]> {
  const map = new Map<string, CheckRow[]>()
  for (const r of rows) {
    const svc = r.group || '未分组'
    if (!map.has(svc)) map.set(svc, [])
    map.get(svc)!.push(r)
  }
  return Array.from(map.entries())
}

export default function SelfCheckModal({ open, onClose }: SelfCheckModalProps) {
  // 先按清单渲染分组骨架(检查中),每项出结果就回填它的状态。
  const [rows, setRows] = useState<CheckRow[]>([])
  const [running, setRunning] = useState(false)
  const [notifySend, setNotifySend] = useState(false)
  const [listError, setListError] = useState('')
  const runIdRef = useRef(0)

  const total = rows.length
  const okCount = rows.filter((r) => r.status === 'ok' || r.status === 'slow').length
  const failCount = rows.filter((r) => r.status === 'fail').length
  const done = rows.filter((r) => r.status !== 'checking').length
  const progress = total === 0 ? 0 : Math.round((done / total) * 100)
  const finished = !running && total > 0 && done >= total

  const runCheck = useCallback(async () => {
    const runId = ++runIdRef.current
    setRunning(true)
    setListError('')
    setRows([])

    let items: Array<{ category: string; key: string; name: string; group: string | null }>
    try {
      const res = await healthApi.selfcheckList()
      items = res.items || []
    } catch (e) {
      if (runId !== runIdRef.current) return
      setListError(e instanceof Error ? e.message : '获取自检清单失败')
      setRunning(false)
      return
    }
    if (runId !== runIdRef.current) return

    const skeleton: CheckRow[] = items.map((it) => ({
      category: it.category, key: it.key, name: it.name, group: it.group,
      status: 'checking', latency_ms: 0, error: null, hint: '', note: null,
    }))
    setRows(skeleton)
    if (skeleton.length === 0) {
      setRunning(false)
      return
    }

    let cursor = 0
    const merge = (key: string, patch: Partial<CheckRow>) => {
      if (runId !== runIdRef.current) return
      setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)))
    }
    const worker = async () => {
      while (true) {
        if (runId !== runIdRef.current) return
        const idx = cursor++
        if (idx >= skeleton.length) return
        const it = skeleton[idx]
        try {
          const res = await healthApi.selfcheckKeys([it.key], notifySend)
          const probed = res.items?.[0]
          merge(it.key, probed
            ? { status: probed.status, latency_ms: probed.latency_ms, error: probed.error, hint: probed.hint, note: probed.note }
            : { status: 'fail', error: '未返回检查结果', hint: '检查请求失败,稍后重试' })
        } catch (e) {
          merge(it.key, { status: 'fail', error: e instanceof Error ? e.message : '请求失败', hint: '检查请求失败,稍后重试' })
        }
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, skeleton.length) }, () => worker()),
    )
    if (runId !== runIdRef.current) return
    setRunning(false)
  }, [notifySend])

  useEffect(() => {
    if (open) void runCheck()
    else runIdRef.current++
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const heroGradient = 'bg-gradient-to-br from-violet-500 via-purple-500 to-indigo-500'

  const renderCategory = (cat: string) => {
    const catRows = rows.filter((r) => r.category === cat)
    if (catRows.length === 0) return null
    return (
      <div key={cat} className="rounded-xl border border-border/40 bg-accent/20 p-3">
        <div className="mb-2 text-[12px] font-semibold text-foreground">
          {CATEGORY_LABELS[cat] ?? cat}
        </div>
        {cat === 'ai' ? (
          <div className="space-y-3">
            {groupByService(catRows).map(([svc, models]) => (
              <div key={svc}>
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
                  {svc}
                </div>
                <div className="ml-3 space-y-2 border-l border-border/40 pl-3">
                  {models.map((m) => <ItemRow key={m.key} item={m} />)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {catRows.map((r) => <ItemRow key={r.key} item={r} />)}
          </div>
        )}
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>系统自检</DialogTitle>
        </DialogHeader>

        {/* 渐变 Hero:进度条 + 总数/正常/异常 */}
        <div className={`relative overflow-hidden rounded-2xl ${heroGradient} p-4 text-white shadow-lg`}>
          <div className="flex items-center justify-between gap-2">
            <div className="text-[13px] font-semibold">
              {running ? '正在检查…' : finished ? '检查完成' : '准备检查'}
            </div>
            <div className="text-[12px] font-mono opacity-90">{progress}%</div>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-white/25">
            <div
              className="h-full rounded-full bg-white transition-all duration-300 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center">
            <div>
              <div className="text-[22px] font-bold leading-none tabular-nums">{total}</div>
              <div className="mt-1 text-[11px] opacity-80">总数</div>
            </div>
            <div>
              <div className="text-[22px] font-bold leading-none tabular-nums">{okCount}</div>
              <div className="mt-1 text-[11px] opacity-80">正常</div>
            </div>
            <div>
              <div className="text-[22px] font-bold leading-none tabular-nums">{failCount}</div>
              <div className="mt-1 text-[11px] opacity-80">异常</div>
            </div>
          </div>
          {finished && failCount > 0 && (
            <div className="mt-3 rounded-lg bg-white/15 px-3 py-1.5 text-[11px]">
              发现 {failCount} 项异常,请查看下方修复建议。
            </div>
          )}
        </div>

        {/* 操作区 */}
        <div className="mt-4 flex items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-[12px] text-muted-foreground cursor-pointer select-none">
            <Switch checked={notifySend} disabled={running} onCheckedChange={setNotifySend} />
            含真实发送通知
          </label>
          <Button size="sm" className="h-8" onClick={() => void runCheck()} disabled={running}>
            <RefreshCw className={`w-3.5 h-3.5 ${running ? 'animate-spin' : ''}`} />
            重新检查
          </Button>
        </div>

        {listError && <div className="mt-3 text-[12px] text-rose-600">{listError}</div>}
        {!listError && total === 0 && !running && (
          <div className="mt-4 rounded-xl border border-border/40 bg-accent/20 p-4 text-center text-[12px] text-muted-foreground">
            未配置 数据源 / AI / 通知,先去设置里配置后再自检。
          </div>
        )}

        {/* 分组明细:数据源 / AI模型(服务商→模型)/ 通知渠道 */}
        <div className="mt-4 space-y-4">{CATEGORY_ORDER.map(renderCategory)}</div>
      </DialogContent>
    </Dialog>
  )
}
