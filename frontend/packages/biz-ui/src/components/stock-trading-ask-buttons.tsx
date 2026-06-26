import type { MouseEvent } from 'react'
import { Sparkles } from 'lucide-react'
import { cn } from '@panwatch/base-ui'
import {
  formatStockTradingAskQuestion,
  type StockTradingAskKind,
} from '@panwatch/biz-ui/lib/stock-trading-ask'

const holdingActions: Array<{ kind: StockTradingAskKind; label: string; className: string }> = [
  { kind: 'add', label: '问加仓', className: 'bg-rose-500/12 text-rose-600 hover:bg-rose-500/20 dark:text-rose-300' },
  { kind: 'reduce', label: '问减仓', className: 'bg-emerald-500/12 text-emerald-600 hover:bg-emerald-500/20 dark:text-emerald-300' },
  { kind: 'clear', label: '问清仓', className: 'bg-red-500/12 text-red-600 hover:bg-red-500/20 dark:text-red-300' },
]

function AskButton(props: {
  label: string
  className: string
  title: string
  compact?: boolean
  onClick: (e: MouseEvent<HTMLButtonElement>) => void
}) {
  const { label, className, title, compact = true, onClick } = props
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-0.5 rounded font-medium transition-colors',
        compact ? 'h-5 px-1.5 text-[9px]' : 'h-7 px-2 text-[11px]',
        className,
      )}
    >
      <Sparkles className={compact ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
      {label}
    </button>
  )
}

export function StockTradingAskButtons(props: {
  stockName: string
  hasPosition: boolean
  compact?: boolean
  className?: string
  onAsk: (question: string, kind: StockTradingAskKind) => void
}) {
  const { stockName, hasPosition, compact = true, className, onAsk } = props

  const handleAsk = (e: MouseEvent, kind: StockTradingAskKind) => {
    e.stopPropagation()
    onAsk(formatStockTradingAskQuestion(stockName, kind), kind)
  }

  return (
    <div
      className={cn('flex flex-wrap items-center gap-1', className)}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {!hasPosition ? (
        <AskButton
          compact={compact}
          label="问建仓"
          title="问 AI：现在是否可以建仓？"
          className="bg-indigo-500/12 text-indigo-600 hover:bg-indigo-500/20 dark:text-indigo-300"
          onClick={(e) => handleAsk(e, 'open')}
        />
      ) : (
        holdingActions.map((item) => (
          <AskButton
            key={item.kind}
            compact={compact}
            label={item.label}
            title={`问 AI：${formatStockTradingAskQuestion(stockName, item.kind)}`}
            className={item.className}
            onClick={(e) => handleAsk(e, item.kind)}
          />
        ))
      )}
    </div>
  )
}
