import { useState, type MouseEvent } from 'react'
import { Plus, RefreshCw } from 'lucide-react'
import { BadgeChip } from '@panwatch/biz-ui/components/badge-chip'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
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
  className?: string
  onUpdateManual?: (tags: string[]) => Promise<void> | void
  onRefreshAuto?: () => Promise<void> | void
}

export function StockConceptTags({
  tags,
  market = 'CN',
  editable = false,
  compact = false,
  maxVisible = 6,
  className,
  onUpdateManual,
  onRefreshAuto,
}: StockConceptTagsProps) {
  const manualTags = tags.filter((t) => t.source === 'manual').map((t) => t.name)
  const visible = tags.slice(0, maxVisible)
  const hiddenCount = Math.max(0, tags.length - visible.length)

  if (!editable && tags.length === 0) {
    return null
  }

  return (
    <ConceptTagsEditor
      tags={tags}
      visible={visible}
      hiddenCount={hiddenCount}
      manualTags={manualTags}
      market={market}
      editable={editable}
      compact={compact}
      className={className}
      onUpdateManual={onUpdateManual}
      onRefreshAuto={onRefreshAuto}
    />
  )
}

function ConceptTagsEditor({
  visible,
  hiddenCount,
  manualTags,
  market,
  editable,
  compact,
  className,
  onUpdateManual,
  onRefreshAuto,
}: {
  tags: StockConceptTagItem[]
  visible: StockConceptTagItem[]
  hiddenCount: number
  manualTags: string[]
  market: string
  editable: boolean
  compact: boolean
  className?: string
  onUpdateManual?: (tags: string[]) => Promise<void> | void
  onRefreshAuto?: () => Promise<void> | void
}) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

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

  return (
    <div
      className={cn('flex flex-wrap items-center gap-1', compact ? 'gap-0.5' : 'gap-1', className)}
      onClick={(e) => e.stopPropagation()}
    >
      {visible.map((tag) => (
        <BadgeChip
          key={`${tag.source}:${tag.name}`}
          size={compact ? 'xs' : 'sm'}
          label={tag.name}
          title={tag.source === 'manual' ? '手动标签（点击移除）' : '自动标签'}
          className={cn(
            tag.source === 'manual'
              ? 'bg-violet-500/15 text-violet-600 dark:text-violet-300'
              : 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
          )}
          onClick={
            editable && tag.source === 'manual'
              ? (e) => removeManual(tag.name, e)
              : undefined
          }
        />
      ))}
      {hiddenCount > 0 && (
        <span className={cn('text-muted-foreground', compact ? 'text-[10px]' : 'text-[11px]')}>
          +{hiddenCount}
        </span>
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
