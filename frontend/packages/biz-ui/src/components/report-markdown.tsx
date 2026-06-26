import { useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, FileDown, List } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { parseReportHeadings, slugifyHeading } from '../lib/report-toc'

const REPORT_PROSE =
  'prose prose-sm dark:prose-invert max-w-none break-words leading-relaxed ' +
  'prose-headings:scroll-mt-4 prose-headings:text-foreground prose-headings:font-semibold ' +
  'prose-h1:text-[18px] prose-h1:mt-0 prose-h1:mb-3 ' +
  'prose-h2:text-[16px] prose-h2:mt-5 prose-h2:mb-2 ' +
  'prose-h3:text-[14px] prose-h3:mt-4 prose-h3:mb-1.5 ' +
  'prose-p:my-2 prose-p:text-foreground/90 ' +
  'prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2 ' +
  'prose-blockquote:border-l-primary/40 prose-blockquote:bg-accent/10 prose-blockquote:py-1 prose-blockquote:px-3 prose-blockquote:not-italic ' +
  'prose-hr:my-4 prose-hr:border-border/50 ' +
  'prose-table:my-3 prose-table:text-[12px] prose-th:px-2 prose-th:py-1.5 prose-td:px-2 prose-td:py-1.5 ' +
  'prose-strong:text-foreground prose-a:text-primary'

function nodeText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children && typeof children === 'object' && 'props' in children) {
    return nodeText((children as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

function headingComponents() {
  return {
    h1: ({ children }: { children?: ReactNode }) => {
      const id = slugifyHeading(nodeText(children))
      return <h1 id={id}>{children}</h1>
    },
    h2: ({ children }: { children?: ReactNode }) => {
      const id = slugifyHeading(nodeText(children))
      return <h2 id={id}>{children}</h2>
    },
    h3: ({ children }: { children?: ReactNode }) => {
      const id = slugifyHeading(nodeText(children))
      return <h3 id={id}>{children}</h3>
    },
    h4: ({ children }: { children?: ReactNode }) => {
      const id = slugifyHeading(nodeText(children))
      return <h4 id={id}>{children}</h4>
    },
  }
}

export function ReportMarkdown({
  content,
  className,
  emptyText = '暂无报告内容',
  withHeadingAnchors = false,
}: {
  content?: string | null
  className?: string
  emptyText?: string
  withHeadingAnchors?: boolean
}) {
  const text = String(content || '').trim()
  if (!text) {
    return <div className="text-[12px] text-muted-foreground">{emptyText}</div>
  }
  return (
    <div className={`${REPORT_PROSE} ${className || ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={withHeadingAnchors ? headingComponents() : undefined}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

export function ReportViewer({
  content,
  className,
  emptyText = '暂无报告内容',
  onExportPdf,
  exportBusy = false,
}: {
  content?: string | null
  className?: string
  emptyText?: string
  onExportPdf?: () => void | Promise<void>
  exportBusy?: boolean
}) {
  const text = String(content || '').trim()
  const headings = useMemo(() => parseReportHeadings(text), [text])
  const [tocOpen, setTocOpen] = useState(true)
  const [activeSlug, setActiveSlug] = useState('')

  if (!text) {
    return <div className="text-[12px] text-muted-foreground">{emptyText}</div>
  }

  const scrollToHeading = (slug: string) => {
    setActiveSlug(slug)
    document.getElementById(slug)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className={`flex flex-col gap-2 ${className || ''}`}>
      <div className="flex items-center justify-between gap-2">
        {headings.length > 0 ? (
          <button
            type="button"
            onClick={() => setTocOpen(v => !v)}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <List className="w-3.5 h-3.5" />
            子目录 ({headings.length})
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${tocOpen ? 'rotate-180' : ''}`} />
          </button>
        ) : (
          <span />
        )}
        {onExportPdf ? (
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-[11px]"
            disabled={exportBusy}
            onClick={() => void onExportPdf()}
          >
            <FileDown className="w-3 h-3 mr-1" />
            {exportBusy ? '导出中…' : '导出 PDF'}
          </Button>
        ) : null}
      </div>

      <div className="flex gap-3 min-h-0">
        {headings.length > 0 && tocOpen ? (
          <nav className="hidden sm:block w-[148px] shrink-0 max-h-[58vh] overflow-y-auto scrollbar pr-1 border-r border-border/30">
            {headings.map(h => (
              <button
                key={`${h.level}-${h.slug}`}
                type="button"
                onClick={() => scrollToHeading(h.slug)}
                className={`block w-full text-left py-1 rounded-md transition-colors truncate ${
                  h.level === 3 ? 'pl-4 pr-1 text-[11px]' : h.level === 4 ? 'pl-6 pr-1 text-[10px]' : 'px-1 text-[12px]'
                } ${
                  activeSlug === h.slug
                    ? 'bg-accent text-foreground font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
                }`}
              >
                {h.text}
              </button>
            ))}
          </nav>
        ) : null}

        <div className="flex-1 min-w-0 max-h-[58vh] overflow-y-auto scrollbar rounded-lg bg-accent/10 p-3">
          <ReportMarkdown content={text} withHeadingAnchors />
        </div>
      </div>
    </div>
  )
}
