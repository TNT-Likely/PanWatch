import { useEffect, useState } from 'react'
import { RefreshCw, Sparkles } from 'lucide-react'
import { stocksApi, type EtfOverview } from '@panwatch/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { EtfNavChart } from './etf-nav-chart'
import { ReportMarkdown } from './report-markdown'

export interface EtfOverviewModalProps {
  code: string
  name?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

type Tab = 'overview' | 'holdings' | 'nav' | 'ai'

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '--'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '--'
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toLocaleString('zh-CN')
}

/**
 * 场内 ETF 详情弹窗 —— 实时行情(IOPV/折价率/规模) + 成分股 + 净值曲线。
 * 数据来自 GET /api/stocks/etf/{code}/overview,各部分独立兜底。
 */
export function EtfOverviewModal({ code, name, open, onOpenChange }: EtfOverviewModalProps) {
  const [data, setData] = useState<EtfOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [tab, setTab] = useState<Tab>('overview')
  const [aiContent, setAiContent] = useState<string>('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState<string>('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await stocksApi.etfOverview(code)
      setData(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载 ETF 详情失败')
    } finally {
      setLoading(false)
    }
  }

  const runAiAnalysis = async () => {
    setAiLoading(true)
    setAiError('')
    setAiContent('')
    try {
      // 无绑定触发(详情弹窗的 ETF 可能未加入自选),wait 同步等结果
      const res = await stocksApi.triggerAgent(0, 'etf_holding_analyst', {
        allow_unbound: true,
        symbol: code,
        market: 'CN',
        name: name || data?.spot?.name || code,
        wait: true,
      })
      const content =
        (res.result as Record<string, unknown> | undefined)?.content as string | undefined
      setAiContent(content || res.message || '分析完成,但未返回内容')
    } catch (e) {
      setAiError(e instanceof Error ? e.message : 'AI 分析失败')
    } finally {
      setAiLoading(false)
    }
  }

  useEffect(() => {
    if (open && code) {
      setTab('overview')
      setAiContent('')
      setAiError('')
      load()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, code])

  const spot = data?.spot
  const holdings = data?.holdings ?? []
  const nav = data?.nav_history ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-[920px] h-[88vh] max-h-[88vh] flex flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Badge variant="outline" className="text-[11px]">ETF</Badge>
            <span className="font-mono text-[15px]">{code}</span>
            <span className="text-foreground">{name || spot?.name}</span>
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto h-7 px-2"
              onClick={load}
              disabled={loading}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex gap-1 px-1 border-b">
          {(['overview', 'holdings', 'nav', 'ai'] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`px-3 py-2 text-[13px] transition-colors border-b-2 -mb-px flex items-center gap-1 ${
                tab === t
                  ? 'border-primary text-foreground font-medium'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t === 'overview'
                ? '概览'
                : t === 'holdings'
                  ? `成分股(${holdings.length})`
                  : t === 'nav'
                    ? '净值曲线'
                    : 'AI 成分分析'}
              {t === 'ai' && <Sparkles className="w-3 h-3" />}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {error && (
            <div className="text-sm text-destructive py-8 text-center">{error}</div>
          )}

          {!error && !data && loading && (
            <div className="text-sm text-muted-foreground py-12 text-center">加载中…</div>
          )}

          {data && tab === 'overview' && (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <Metric label="最新价" value={fmtNum(spot?.price)} accent={spot?.change_pct != null && spot.change_pct >= 0 ? 'up' : 'down'} />
              <Metric label="涨跌幅" value={fmtPct(spot?.change_pct)} accent={spot?.change_pct != null && spot.change_pct >= 0 ? 'up' : 'down'} />
              <Metric label="IOPV 实时估值" value={fmtNum(spot?.iopv)} />
              <Metric label="折溢价率" value={fmtPct(spot?.premium_pct)} hint="正为溢价,负为折价" />
              <Metric label="基金规模" value={fmtMoney(spot?.total_value)} />
              <Metric label="成交额" value={fmtMoney(spot?.turnover)} />
              <Metric label="换手率" value={spot?.turnover_rate != null ? `${spot.turnover_rate.toFixed(2)}%` : '--'} />
              <Metric label="成交量" value={fmtMoney(spot?.volume)} />
            </div>
          )}

          {data && tab === 'holdings' && (
            holdings.length === 0 ? (
              <div className="text-sm text-muted-foreground py-12 text-center">暂无成分股数据(季报披露后更新)</div>
            ) : (
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-muted-foreground text-left border-b">
                    <th className="py-2 font-normal w-12">#</th>
                    <th className="py-2 font-normal">代码</th>
                    <th className="py-2 font-normal">名称</th>
                    <th className="py-2 font-normal text-right">占净值</th>
                    <th className="py-2 font-normal w-1/3">权重</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h, i) => {
                    const maxW = holdings[0]?.weight_pct || 1
                    return (
                      <tr key={h.symbol} className="border-b border-border/40">
                        <td className="py-2 text-muted-foreground">{i + 1}</td>
                        <td className="py-2 font-mono">{h.symbol}</td>
                        <td className="py-2">{h.name}</td>
                        <td className="py-2 text-right tabular-nums">{h.weight_pct.toFixed(2)}%</td>
                        <td className="py-2 pr-2">
                          <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full bg-primary/60"
                              style={{ width: `${Math.min(100, (h.weight_pct / maxW) * 100)}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )
          )}

          {data && tab === 'nav' && (
            nav.length === 0 ? (
              <div className="text-sm text-muted-foreground py-12 text-center">暂无净值数据</div>
            ) : (
              <div>
                <div className="flex gap-2 mb-3 text-[12px] text-muted-foreground">
                  <span>区间: {nav[0]?.date} ~ {nav[nav.length - 1]?.date}</span>
                  <span>·</span>
                  <span>最新单位净值: {fmtNum(nav[nav.length - 1]?.unit_nav)}</span>
                </div>
                <EtfNavChart data={nav} field="unit_nav" height={280} />
              </div>
            )
          )}

          {data && tab === 'ai' && (
            <div>
              {!aiContent && !aiLoading && !aiError && (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <Sparkles className="w-6 h-6 text-primary/60" />
                  <div className="text-[13px] text-muted-foreground text-center max-w-sm">
                    分析 ETF 成分股集中度、折溢价、与持仓重叠,生成结构化操作建议
                  </div>
                  <Button onClick={runAiAnalysis} size="sm">
                    <Sparkles className="w-3.5 h-3.5 mr-1" />
                    开始 AI 分析
                  </Button>
                </div>
              )}
              {aiLoading && (
                <div className="flex flex-col items-center justify-center py-12 gap-2">
                  <RefreshCw className="w-5 h-5 animate-spin text-primary/60" />
                  <div className="text-[13px] text-muted-foreground">
                    正在分析成分股与持仓重叠…(约 10-30 秒)
                  </div>
                </div>
              )}
              {aiError && (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <div className="text-sm text-destructive">{aiError}</div>
                  <Button onClick={runAiAnalysis} variant="outline" size="sm">
                    重试
                  </Button>
                </div>
              )}
              {aiContent && (
                <div>
                  <div className="flex justify-end mb-2">
                    <Button onClick={runAiAnalysis} variant="ghost" size="sm" disabled={aiLoading}>
                      <RefreshCw className={`w-3.5 h-3.5 mr-1 ${aiLoading ? 'animate-spin' : ''}`} />
                      重新分析
                    </Button>
                  </div>
                  <ReportMarkdown content={aiContent} />
                </div>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Metric({
  label,
  value,
  hint,
  accent,
}: {
  label: string
  value: string
  hint?: string
  accent?: 'up' | 'down'
}) {
  const color =
    accent === 'up' ? 'text-red-500' : accent === 'down' ? 'text-green-500' : 'text-foreground'
  return (
    <div className="rounded-lg border bg-card/50 px-3 py-2.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`text-[18px] font-semibold tabular-nums ${color}`}>{value}</div>
      {hint && <div className="text-[10px] text-muted-foreground/70 mt-0.5">{hint}</div>}
    </div>
  )
}

export default EtfOverviewModal
