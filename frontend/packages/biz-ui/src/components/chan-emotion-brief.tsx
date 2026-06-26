import { cn } from '@panwatch/base-ui'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'
import type { ChanEmotionBrief } from '@panwatch/api/insight'
import type { MouseEvent } from 'react'

function emotionTone(phase?: string): 'bullish' | 'bearish' | 'neutral' {
  if (phase === 'profit_effect') return 'bullish'
  if (phase === 'loss_effect') return 'bearish'
  return 'neutral'
}

function emotionShortLabel(label?: string): string {
  if (!label) return '情绪'
  return label.split('（')[0] || label
}

export function ChanEmotionBrief(props: {
  data?: ChanEmotionBrief | null
  loading?: boolean
  compact?: boolean
  className?: string
  onClick?: () => void
  onRequestLoad?: () => void
}) {
  const { data, loading = false, compact = true, className, onClick, onRequestLoad } = props

  if (loading && !data) {
    return (
      <div className={cn('flex flex-wrap items-center gap-1', className)}>
        <span className={cn('text-muted-foreground/60', compact ? 'text-[9px]' : 'text-[10px]')}>
          缠论分析中…
        </span>
      </div>
    )
  }

  if (!data) {
    if (!onRequestLoad) return null
    const handleRequestLoad = (e: MouseEvent) => {
      e.stopPropagation()
      onRequestLoad()
    }
    return (
      <div className={cn('flex flex-wrap items-center gap-1', className)}>
        <button
          type="button"
          className={cn(
            'inline-flex items-center rounded bg-muted/50 px-1 py-px text-muted-foreground hover:bg-muted hover:text-foreground transition-colors',
            compact ? 'text-[9px]' : 'text-[10px]',
          )}
          title="点击查看缠论情绪策略"
          onClick={handleRequestLoad}
        >
          缠论
        </button>
      </div>
    )
  }

  const title = data.reason || `${data.action_label} · 赢面 ${data.win_rate}% · ${data.emotion_label}`

  const handleClick = (e: MouseEvent) => {
    e.stopPropagation()
    onClick?.()
  }

  return (
    <div
      className={cn('flex flex-wrap items-center gap-1', onClick && 'cursor-pointer', className)}
      title={title}
      onClick={onClick ? handleClick : undefined}
    >
      <TechnicalBadge
        label={data.action_label}
        tone={technicalToneFromSuggestionAction(data.action, data.action_label)}
        size={compact ? 'xs' : 'sm'}
      />
      <TechnicalBadge
        label={`赢面 ${data.win_rate}%`}
        tone={data.win_rate >= 70 ? 'bullish' : data.win_rate < 55 ? 'bearish' : 'neutral'}
        size={compact ? 'xs' : 'sm'}
      />
      <TechnicalBadge
        label={emotionShortLabel(data.emotion_label)}
        tone={emotionTone(data.emotion_phase)}
        size={compact ? 'xs' : 'sm'}
      />
    </div>
  )
}
