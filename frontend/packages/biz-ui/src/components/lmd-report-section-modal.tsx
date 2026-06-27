import { useEffect, useState } from 'react'
import { insightApi } from '@panwatch/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { ReportViewer } from './report-markdown'
import { findLmdReportSectionSlug, type LmdReportSection } from '../lib/report-toc'
import { isLmdReportAgent, LMD_DISPLAY_NAME, pickLatestLmdReport } from '../lib/lmd-report'

const SECTION_LABELS: Record<LmdReportSection, string> = {
  valuation: '估值',
  fundamentals: '基本面',
}

interface LmdHistoryRecord {
  id: number
  agent_name: string
  title?: string
  content?: string
  analysis_date?: string
  updated_at?: string
  created_at?: string
}

export function LmdReportSectionModal({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  section,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  section: LmdReportSection | null
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [content, setContent] = useState('')
  const [reportTitle, setReportTitle] = useState('')
  const [sectionSlug, setSectionSlug] = useState<string | null>(null)

  const sectionLabel = section ? SECTION_LABELS[section] : ''

  useEffect(() => {
    if (!open || !symbol || !section) return
    let cancelled = false
    setLoading(true)
    setError('')
    setContent('')
    setReportTitle('')
    setSectionSlug(null)

    ;(async () => {
      try {
        const records = await insightApi.history<LmdHistoryRecord[]>({
          stock_symbol: symbol,
          kind: 'all',
          limit: 50,
        })
        const report = pickLatestLmdReport(
          (records || []).filter(r => isLmdReportAgent(r.agent_name)),
        )
        if (!report?.content?.trim()) {
          if (!cancelled) setError(`暂无${LMD_DISPLAY_NAME}报告，请先在报告页生成`)
          return
        }
        const slug = findLmdReportSectionSlug(report.content, section)
        if (!cancelled) {
          setContent(report.content)
          setReportTitle(report.title || LMD_DISPLAY_NAME)
          setSectionSlug(slug)
          if (!slug) setError(`报告中未找到「${SECTION_LABELS[section]}」章节`)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : `加载${LMD_DISPLAY_NAME}报告失败`)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [open, symbol, market, section])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col gap-3 p-4 sm:p-5">
        <DialogHeader className="shrink-0 space-y-1">
          <DialogTitle className="text-base leading-snug pr-6">
            {stockName || symbol}
            <span className="ml-1.5 font-mono text-[13px] text-muted-foreground font-normal">{symbol}</span>
            <span className="mx-1.5 text-muted-foreground/50">·</span>
            {LMD_DISPLAY_NAME} · {sectionLabel}
          </DialogTitle>
          {reportTitle ? (
            <DialogDescription className="text-[11px] line-clamp-1">{reportTitle}</DialogDescription>
          ) : (
            <DialogDescription className="sr-only">{LMD_DISPLAY_NAME}报告章节预览</DialogDescription>
          )}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="rounded-lg border border-border/40 bg-accent/20 px-4 py-8 text-center text-[13px] text-muted-foreground">
              {error}
            </div>
          ) : (
            <ReportViewer content={content} initialSectionSlug={sectionSlug} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
