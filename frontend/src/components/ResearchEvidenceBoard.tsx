import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { markdownClass, type EvidenceItem } from './ResearchReport'

export function ResearchEvidenceBoard({ items }: { items: EvidenceItem[] }) {
  const { t } = useTranslation('configuration')
  const [stock, setStock] = useState('all')
  const [domain, setDomain] = useState('all')
  const [expanded, setExpanded] = useState(false)
  const stocks = [...new Map(items.filter(item => item.stock_key).map(item => [item.stock_key!, item.stock_name || item.stock_key!])).entries()]
  const domains = [...new Set(items.map(item => item.domain).filter(Boolean))] as string[]
  const filtered = items.filter(item => (stock === 'all' || item.stock_key === stock) && (domain === 'all' || item.domain === domain))
  return <section className="space-y-3" aria-label={t('p4.history.evidenceTab')}>
    <div className="sticky top-0 z-10 space-y-2 rounded-xl border border-border/60 bg-card p-3">
      <div className="flex flex-wrap gap-1.5">
        <Button variant={stock === 'all' ? 'secondary' : 'ghost'} size="sm" aria-pressed={stock === 'all'} onClick={() => setStock('all')}>{t('p4.history.allStocks')}</Button>
        {stocks.map(([key, name]) => <Button key={key} variant={stock === key ? 'secondary' : 'ghost'} size="sm" className="h-auto min-h-8 whitespace-normal break-words text-xs" aria-pressed={stock === key} onClick={() => setStock(key)}>{name} · {key}</Button>)}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={domain} onValueChange={setDomain}><SelectTrigger aria-label={t('p4.history.evidenceType')} className="h-8 w-[140px] text-xs"><SelectValue /></SelectTrigger><SelectContent>
          <SelectItem value="all">{t('p4.history.allEvidence')}</SelectItem>{domains.map(value => <SelectItem key={value} value={value}>{t(`p4.history.domains.${value}`, { defaultValue: value })}</SelectItem>)}
        </SelectContent></Select>
        <Button size="sm" variant="outline" onClick={() => setExpanded(!expanded)}>{t(expanded ? 'p4.history.collapseAll' : 'p4.history.expandAll')}</Button>
        <span className="text-xs text-muted-foreground" aria-live="polite">{t('p4.history.evidenceCount', { count: filtered.length })}</span>
      </div>
    </div>
    <div className="grid min-w-0 gap-3 xl:grid-cols-2">
      {filtered.map((item, index) => <article key={item.id || index} className="min-w-0 space-y-2 rounded-xl border border-border/60 bg-background/60 p-3">
        <h3 className="break-words text-sm font-semibold">{item.stock_name ? `${item.stock_name} · ${item.domain_label}` : item.title}</h3>
        {item.stock_key && <p className="text-[11px] text-muted-foreground">{item.stock_key} · {item.providers?.join(' / ')}<br />{item.captured_at}</p>}
        <div className={markdownClass}><ReactMarkdown>{expanded || !item.summary ? item.content : item.summary}</ReactMarkdown></div>
        {!expanded && item.summary && !!item.items?.length && <ul className="space-y-2 text-xs">{item.items.slice(0, 3).map((news, i) => <li key={i}>
          {/^https?:\/\//.test(news.url) ? <a href={news.url} target="_blank" rel="noreferrer" className="break-words underline underline-offset-2">{news.title}</a> : <span>{news.title}</span>}
          <p className="mt-1 break-words text-[11px] text-muted-foreground">{news.source} · {news.time}</p>
        </li>)}</ul>}
        {!expanded && (item.items?.length || 0) > 3 && <p className="text-xs text-muted-foreground">{t('p4.history.moreEvidence', { count: item.items!.length - 3 })}</p>}
      </article>)}
    </div>
    {!filtered.length && <p className="py-6 text-center text-muted-foreground">{t('p4.history.noEvidence')}</p>}
  </section>
}
