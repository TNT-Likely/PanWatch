import { cn } from '@panwatch/base-ui'
import type { IndustryChainInfo } from '@panwatch/api'
import {
  CHAIN_LAYER_BADGE_STYLES,
  formatIndustryChainDisplay,
  normalizeChainLayer,
} from '../lib/ai-chain-rotation'

const ROTATION_FRAMEWORK =
  '轮动框架：GPU→CPO→HBM→PCB→液冷→材料设备→服务器→IDC→电力→物理AI（云&大模型、软件应用 A股跳过）'

const A_SHARE_SKIP_LAYERS = new Set(['cloud_llm', 'software_app'])

function formatMatchSource(source?: string): string {
  if (source === 'symbol') return '龙头代码白名单直配'
  if (source === 'keyword') return 'AI 赛道 + 轮动环节关键词匹配'
  if (source === 'ai') return 'AI 结合名称/行业/概念归类'
  if (source === 'manual') return '手动指定'
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
    lines.push(`环节说明：${chain.description}`)
  }
  if (chain.layer !== 'other') {
    lines.push(ROTATION_FRAMEWORK)
  }
  if (A_SHARE_SKIP_LAYERS.has(normalizeChainLayer(chain.layer))) {
    lines.push('投资提示：A股此环节建议跳过（美股例外）')
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
  const displayLabel = formatIndustryChainDisplay(chain)
  if (!displayLabel) return null

  const layerKey = normalizeChainLayer(chain.layer)
  const layerStyle = CHAIN_LAYER_BADGE_STYLES[layerKey] || 'bg-accent/50 text-muted-foreground'

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onClick?.()
      }}
      className={cn(
        'inline-flex shrink-0 rounded transition-opacity hover:opacity-80',
        compact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5',
        layerStyle,
        className,
      )}
    >
      {displayLabel}
    </button>
  )
}
