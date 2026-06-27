import { useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import type { IndustryChainInfo } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { cn } from '@panwatch/base-ui'
import {
  AI_COMPUTE_STEPS,
  AI_POST_COMPUTE_PHASES,
} from '../lib/ai-chain-rotation'
import { IndustryChainBadge } from './industry-chain-badge'

interface StockIndustryChainEditorProps {
  chain?: IndustryChainInfo | null
  manualLayer?: string
  className?: string
  onUpdateManual?: (layer: string | null) => Promise<void> | void
  onRefreshAuto?: () => Promise<void> | void
}

const MANUAL_SENTINEL = '__unset__'

export function StockIndustryChainEditor({
  chain,
  manualLayer = '',
  className,
  onUpdateManual,
  onRefreshAuto,
}: StockIndustryChainEditorProps) {
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const currentLayer = manualLayer || chain?.layer || ''
  const isManual = chain?.source === 'manual' || !!manualLayer

  const handleSelect = async (value: string) => {
    if (!onUpdateManual) return
    setSaving(true)
    try {
      if (value === MANUAL_SENTINEL) return
      await onUpdateManual(value === 'other' ? 'other' : value)
    } finally {
      setSaving(false)
    }
  }

  const clearManual = async () => {
    if (!onUpdateManual) return
    setSaving(true)
    try {
      await onUpdateManual(null)
    } finally {
      setSaving(false)
    }
  }

  const refreshAuto = async () => {
    if (!onRefreshAuto) return
    setRefreshing(true)
    try {
      await onRefreshAuto()
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div
      className={cn('flex flex-wrap items-center gap-2', className)}
      onClick={(e) => e.stopPropagation()}
    >
      {chain?.layer ? (
        <IndustryChainBadge chain={chain} compact />
      ) : (
        <span className="text-[11px] text-muted-foreground">未分类</span>
      )}
      {isManual ? (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-600">
          手动
        </span>
      ) : chain?.match_source === 'ai' ? (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-700">
          AI
        </span>
      ) : chain?.match_source === 'symbol' ? (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-700">
          白名单
        </span>
      ) : null}
      {onUpdateManual && (
        <Select
          value={currentLayer || MANUAL_SENTINEL}
          onValueChange={(value) => {
            if (value === MANUAL_SENTINEL) return
            handleSelect(value).catch(() => undefined)
          }}
          disabled={saving}
        >
          <SelectTrigger className="h-7 w-[132px] text-[11px]">
            <SelectValue placeholder="调整环节" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={MANUAL_SENTINEL} disabled className="hidden">
              调整环节
            </SelectItem>
            <SelectGroup>
              <SelectLabel>算力细分</SelectLabel>
              {AI_COMPUTE_STEPS.map((theme) => (
                <SelectItem key={theme.key} value={theme.key} className="text-[12px]">
                  {theme.label}
                </SelectItem>
              ))}
            </SelectGroup>
            <SelectGroup>
              <SelectLabel>A股主线</SelectLabel>
              {AI_POST_COMPUTE_PHASES.map((theme) => (
                <SelectItem key={theme.key} value={theme.key} className="text-[12px]">
                  {theme.label}
                  {theme.skipAshare ? '（A股跳过）' : ''}
                </SelectItem>
              ))}
            </SelectGroup>
            <SelectGroup>
              <SelectLabel>其他</SelectLabel>
              <SelectItem value="other" className="text-[12px]">其他</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
      )}
      {isManual && onUpdateManual && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          disabled={saving}
          title="恢复自动分类"
          onClick={() => clearManual().catch(() => undefined)}
        >
          <X className="w-3.5 h-3.5" />
        </Button>
      )}
      {onRefreshAuto && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          disabled={refreshing || saving}
          title="重新 AI 分类"
          onClick={() => refreshAuto().catch(() => undefined)}
        >
          <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
        </Button>
      )}
    </div>
  )
}
