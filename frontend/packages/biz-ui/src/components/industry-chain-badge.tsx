import { HelpCircle } from 'lucide-react'
import { cn } from '@panwatch/base-ui'
import type { IndustryChainInfo } from '@panwatch/api'

const LAYER_STYLES: Record<string, string> = {
  foundation: 'bg-sky-500/15 text-sky-600',
  middleware: 'bg-violet-500/15 text-violet-600',
  integration: 'bg-amber-500/15 text-amber-600',
  application: 'bg-emerald-500/15 text-emerald-600',
  other: 'bg-slate-500/15 text-slate-600',
}

function formatMatchSource(source?: string): string {
  if (source === 'symbol') return '龙头代码白名单直配'
  if (source === 'keyword') return 'AI 赛道 + 产业链层级关键词匹配'
  if (source === 'fallback') return '未命中人工智能赛道'
  return '自动归类'
}

function formatMatchedItems(matched?: string[]): string[] {
  if (!matched?.length) return []
  const out: string[] = []
  for (const raw of matched) {
    const item = (raw || '').trim()
    if (!item) continue
    if (item.startsWith('symbol:')) {
      out.push(`白名单代码 ${item.slice('symbol:'.length)}`)
      continue
    }
    out.push(item)
  }
  return out
}

export function buildIndustryChainBasis(chain: IndustryChainInfo): string[] {
  const lines: string[] = []
  if (chain.description) {
    lines.push(`层级说明：${chain.description}`)
  }
  if (chain.layer !== 'other') {
    lines.push('分层框架：底层 → 中间件 → 集成 → 应用')
  }
  lines.push(`归类方式：${formatMatchSource(chain.match_source)}`)
  const items = formatMatchedItems(chain.matched)
  if (items.length) {
    lines.push(`命中依据：${items.join('、')}`)
  }
  return lines
}

interface IndustryChainBadgeProps {
  chain: IndustryChainInfo
  compact?: boolean
  className?: string
  onClick?: () => void
}

export function IndustryChainBadge({
  chain,
  compact = false,
  className,
  onClick,
}: IndustryChainBadgeProps) {
  if (!chain.display) return null

  const basisLines = buildIndustryChainBasis(chain)
  const layerStyle = LAYER_STYLES[chain.layer] || 'bg-accent/50 text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center gap-0.5 shrink-0', className)}>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onClick?.()
        }}
        className={cn(
          'rounded transition-opacity hover:opacity-80',
          compact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5',
          layerStyle,
        )}
      >
        {chain.display}
      </button>
      <span
        className="relative inline-flex group/chain-help"
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <HelpCircle
          className={cn(
            'text-muted-foreground/70 group-hover/chain-help:text-muted-foreground cursor-help',
            compact ? 'w-3 h-3' : 'w-3.5 h-3.5',
          )}
          aria-label="查看产业链分类依据"
        />
        <span
          role="tooltip"
          className={cn(
            'pointer-events-none absolute left-1/2 bottom-[calc(100%+4px)] z-[60] w-max max-w-[260px] -translate-x-1/2',
            'rounded-md border border-border/60 bg-popover px-2.5 py-2 text-[10px] leading-relaxed text-popover-foreground shadow-lg',
            'opacity-0 invisible group-hover/chain-help:opacity-100 group-hover/chain-help:visible',
            'transition-opacity duration-150',
          )}
        >
          <span className="block font-medium text-[11px] mb-1">分类依据</span>
          {basisLines.map((line) => (
            <span key={line} className="block text-muted-foreground">
              {line}
            </span>
          ))}
        </span>
      </span>
    </span>
  )
}
