import { useId, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslation } from 'react-i18next'
import { markdownClass, type ProcessItem } from './ResearchReport'

const LAYERS = [['gate'], ['technical', 'flow', 'events'], ['bull', 'bear'], ['manager'], ['strategy'], ['risk'], ['decision'], ['execution']]

export function ResearchProcessGraph({ items }: { items: ProcessItem[] }) {
  const { t } = useTranslation('configuration')
  const marker = useId().replace(/:/g, '')
  const [selected, setSelected] = useState('manager')
  const groups = useMemo(() => items.reduce<Record<string, ProcessItem[]>>((acc, item) => {
    if (item.role) (acc[item.role] ||= []).push(item)
    return acc
  }, {}), [items])
  const layers = LAYERS.map(layer => layer.filter(role => groups[role]?.length)).filter(layer => layer.length)
  const positions = Object.fromEntries(layers.flatMap((layer, row) => layer.map((role, col) => [role, { x: (col + .5) * 1000 / layer.length, y: row * 92 + 40, half: layer.length === 3 ? 145 : layer.length === 2 ? 210 : 310, width: layer.length === 3 ? '29%' : layer.length === 2 ? '42%' : '62%' }])))
  const edges: { from: string; to: string; feedback?: boolean }[] = []
  layers.forEach((layer, index) => {
    if (index) for (const from of layers[index - 1]) for (const to of layer) edges.push({ from, to })
  })
  for (const item of items) {
    if (item.role === 'risk' && item.verdict === 'revise') edges.push({ from: 'risk', to: 'strategy', feedback: true })
    if (['evidence_needed', 'rebuttal'].includes(item.verdict || '')) for (const to of item.feedback_roles || []) {
      if (item.role && positions[to]) edges.push({ from: item.role, to, feedback: true })
    }
  }
  const active = groups[selected]?.length ? selected : (groups.strategy?.length ? 'strategy' : layers[layers.length - 1]?.[0])
  const label = (role: string) => role === 'strategy' && groups.gate?.length ? t('p4.history.intradayDecision') : role === 'risk' && groups[role]?.every(item => item.kind === 'rule') ? t('p4.history.ruleChecks') : t(`p4.history.graphRoles.${role}`, { defaultValue: role })
  const height = layers.length * 92
  const selectedItems = active ? groups[active] || [] : items
  return <section className="space-y-3" aria-label={t('p4.history.processTab')}>
    <p className="text-xs leading-5 text-muted-foreground">{t('p4.history.graphHint')}</p>
    {layers.length > 0 && <div className="relative rounded-xl border border-border/60 bg-background/60" style={{ height }} data-testid="research-graph">
      <svg className="absolute inset-0 h-full w-full text-muted-foreground/60" viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none" aria-hidden="true">
        <defs><marker id={marker} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="currentColor" /></marker></defs>
        {edges.filter(edge => positions[edge.from] && positions[edge.to]).map((edge, index) => {
          const a = positions[edge.from], b = positions[edge.to], mid = (a.y + b.y) / 2
          const path = edge.feedback ? `M${a.x + a.half},${a.y} C990,${a.y} 990,${b.y} ${b.x + b.half},${b.y}` : `M${a.x},${a.y + 36} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y - 36}`
          return <path key={index} d={path} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeDasharray={edge.feedback ? '4 4' : undefined} markerEnd={`url(#${marker})`} data-feedback={edge.feedback || undefined} />
        })}
      </svg>
      {Object.entries(positions).map(([role, p]) => <button key={role} type="button" aria-pressed={active === role} onClick={() => setSelected(role)}
        className={`absolute flex h-[72px] -translate-x-1/2 flex-col items-center justify-center gap-1 rounded-lg border px-1 text-center text-xs shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${active === role ? 'border-primary bg-accent text-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/50'}`}
        style={{ left: `${p.x / 10}%`, top: p.y - 36, width: p.width }}>
        <span className="font-medium">{label(role)}</span><span className="text-[10px]">{t('p4.history.artifactCount', { count: groups[role]?.length || 0 })}</span>
      </button>)}
    </div>}
    {edges.some(edge => edge.feedback) && <p className="text-xs text-muted-foreground">{t('p4.history.feedbackLegend')}</p>}
    {active && <h3 className="text-sm font-semibold">{label(active)}</h3>}
    {selectedItems.map((item, index) => <article key={item.id || index} className="rounded-xl border border-border/60 bg-background/60 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{item.title}</span>{item.kind === 'rule' && <span className="rounded bg-accent px-1.5 py-0.5">{t('p4.history.ruleNode')}</span>}{item.verdict && <span className="rounded bg-accent px-1.5 py-0.5">{t(`p4.history.verdict.${item.verdict}`, { defaultValue: item.verdict })}</span>}{item.active === false && <span>{t('p4.history.previousRound')}</span>}</div>
      <div className={markdownClass}><ReactMarkdown>{item.content}</ReactMarkdown></div>
    </article>)}
    {!items.length && <p className="py-6 text-center text-muted-foreground">{t('p4.history.noProcess')}</p>}
  </section>
}
