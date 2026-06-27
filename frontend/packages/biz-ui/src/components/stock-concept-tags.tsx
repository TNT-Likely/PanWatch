import { useState, type MouseEvent } from 'react'
import { Plus, RefreshCw } from 'lucide-react'
import { BadgeChip } from '@panwatch/biz-ui/components/badge-chip'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import { cn } from '@panwatch/base-ui'

export interface StockConceptTagItem {
  name: string
  source: 'auto' | 'manual' | string
}

interface StockConceptTagsProps {
  tags: StockConceptTagItem[]
  market?: string
  editable?: boolean
  compact?: boolean
  maxVisible?: number
  /** 初始是否展开全部标签（用于股票详情等场景） */
  defaultExpanded?: boolean
  className?: string
  activeTag?: string
  onTagClick?: (name: string) => void
  onUpdateManual?: (tags: string[]) => Promise<void> | void
  onRefreshAuto?: () => Promise<void> | void
}

export function StockConceptTags({
  tags,
  market = 'CN',
  editable = false,
  compact = false,
  maxVisible = 6,
  defaultExpanded = false,
  className,
  activeTag = '',
  onTagClick,
  onUpdateManual,
  onRefreshAuto,
}: StockConceptTagsProps) {
  const manualTags = tags.filter((t) => t.source === 'manual').map((t) => t.name)

  if (!editable && tags.length === 0) {
    return null
  }

  return (
    <ConceptTagsEditor
      tags={tags}
      maxVisible={maxVisible}
      defaultExpanded={defaultExpanded}
      manualTags={manualTags}
      market={market}
      editable={editable}
      compact={compact}
      className={className}
      activeTag={activeTag}
      onTagClick={onTagClick}
      onUpdateManual={onUpdateManual}
      onRefreshAuto={onRefreshAuto}
    />
  )
}

function ConceptTagsEditor({
  tags,
  maxVisible,
  defaultExpanded,
  manualTags,
  market,
  editable,
  compact,
  className,
  activeTag,
  onTagClick,
  onUpdateManual,
  onRefreshAuto,
}: {
  tags: StockConceptTagItem[]
  maxVisible: number
  defaultExpanded: boolean
  manualTags: string[]
  market: string
  editable: boolean
  compact: boolean
  className?: string
  activeTag?: string
  onTagClick?: (name: string) => void
  onUpdateManual?: (tags: string[]) => Promise<void> | void
  onRefreshAuto?: () => Promise<void> | void
}) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [expanded, setExpanded] = useState(defaultExpanded)

  const visibleTags = expanded ? tags : tags.slice(0, maxVisible)
  const hiddenTags = expanded ? [] : tags.slice(maxVisible)
  const hiddenCount = hiddenTags.length

  const saveManual = async (nextManual: string[]) => {
    if (!onUpdateManual) return
    setSaving(true)
    try {
      await onUpdateManual(nextManual)
    } finally {
      setSaving(false)
    }
  }

  const addDraft = async () => {
    const value = draft.trim()
    if (!value || manualTags.includes(value)) {
      setDraft('')
      return
    }
    await saveManual([...manualTags, value])
    setDraft('')
  }

  const removeManual = async (name: string, e?: MouseEvent) => {
    e?.stopPropagation()
    await saveManual(manualTags.filter((t) => t !== name))
  }

  const refreshAuto = async (e?: MouseEvent) => {
    e?.stopPropagation()
    if (!onRefreshAuto || market !== 'CN') return
    setRefreshing(true)
    try {
      await onRefreshAuto()
    } finally {
      setRefreshing(false)
    }
  }

  const renderTagChip = (tag: StockConceptTagItem) => {
    const isActive = !!activeTag && activeTag === tag.name
    return (
      <BadgeChip
        key={`${tag.source}:${tag.name}`}
        size={compact ? 'xs' : 'sm'}
        label={tag.name}
        title={
          onTagClick
            ? '点击筛选该标签'
            : tag.source === 'manual'
              ? '手动标签（点击移除）'
              : '自动标签'
        }
        className={cn(
          tag.source === 'manual'
            ? 'bg-violet-500/15 text-violet-600 dark:text-violet-300'
            : 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
          isActive && 'ring-1 ring-primary/60',
          onTagClick && 'cursor-pointer hover:opacity-80',
        )}
        onClick={
          onTagClick
            ? (e) => {
                e.stopPropagation()
                onTagClick(tag.name)
              }
            : editable && tag.source === 'manual'
              ? (e) => removeManual(tag.name, e)
              : undefined
        }
      />
    )
  }

  const hoverPreviewTags = compact ? tags : hiddenTags

  return (
    <div
      className={cn('flex flex-wrap items-center gap-1', compact ? 'gap-0.5' : 'gap-1', className)}
      onClick={(e) => e.stopPropagation()}
    >
      {visibleTags.map(renderTagChip)}
      {hiddenCount > 0 && (
        <HoverPopover
          side="top"
          align="start"
          popoverClassName="w-auto max-w-[min(22rem,90vw)]"
          title={compact ? '全部标签' : `还有 ${hiddenCount} 个标签`}
          trigger={
            <button
              type="button"
              className={cn(
                'rounded px-1 text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground',
                compact ? 'text-[10px]' : 'text-[11px]',
              )}
              title={compact ? '悬停查看全部标签' : '悬停预览，点击展开全部'}
              onClick={(e) => {
                e.stopPropagation()
                if (!compact) {
                  setExpanded(true)
                }
              }}
            >
              +{hiddenCount}
            </button>
          }
          content={
            <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
              {hoverPreviewTags.map(renderTagChip)}
            </div>
          }
        />
      )}
      {editable && (
        <div className="flex items-center gap-1 w-full sm:w-auto mt-1 sm:mt-0">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addDraft().catch(() => undefined)
              }
            }}
            placeholder="添加标签"
            className={cn('h-7', compact ? 'w-[88px] text-[11px]' : 'w-[110px] text-[12px]')}
            disabled={saving}
          />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="h-7 px-2"
            disabled={saving || !draft.trim()}
            onClick={() => addDraft().catch(() => undefined)}
          >
            <Plus className="w-3.5 h-3.5" />
          </Button>
          {market === 'CN' && onRefreshAuto && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2"
              disabled={refreshing}
              title="重新拉取东财概念标签"
              onClick={(e) => refreshAuto(e).catch(() => undefined)}
            >
              <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
