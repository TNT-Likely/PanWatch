import { ChevronRight } from 'lucide-react'
import { cn } from '@panwatch/base-ui'
import {
  AI_COMPUTE_STEPS,
  AI_POST_COMPUTE_PHASES,
  CHAIN_HOT_LAYER,
  CHAIN_NEXT_LAYER,
  CHAIN_THEME_LAYER,
  chainLayerFilterKey,
  type ChainLayerTheme,
} from '../lib/ai-chain-rotation'

export interface ChainRotationFilterOption {
  key: string
  layer: string
  count: number
}

interface AiChainRotationBannerProps {
  layerCounts?: ChainRotationFilterOption[]
  activeFilterKey?: string
  onToggleFilter?: (key: string) => void
  className?: string
}

function countForLayer(
  layerKey: string,
  layerCounts?: ChainRotationFilterOption[],
): number {
  const filterKey = chainLayerFilterKey(layerKey)
  return layerCounts?.find((opt) => opt.key === filterKey || opt.layer === layerKey)?.count ?? 0
}

function RotationNode({
  theme,
  count,
  active,
  pulse,
  badge,
  onClick,
  size = 'md',
}: {
  theme: ChainLayerTheme
  count: number
  active: boolean
  pulse?: 'hot' | 'next' | 'theme' | null
  badge?: string
  onClick?: () => void
  size?: 'md' | 'lg'
}) {
  const skipped = theme.skipAshare
  const large = size === 'lg'

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group/node relative flex flex-col items-center rounded-lg transition-all',
        large ? 'gap-1 min-w-[56px] px-1 py-2' : 'gap-0.5 min-w-[52px] px-0.5 py-1.5',
        skipped && 'opacity-55',
        active && 'bg-primary/10 ring-2 ring-primary/30',
        !active && 'hover:bg-accent/40',
      )}
      title={
        skipped
          ? `${theme.label}（A股建议跳过）`
          : theme.label
      }
    >
      {badge ? (
        <span
          className={cn(
            'absolute left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md font-semibold leading-none shadow-sm',
            large ? '-top-3 px-2 py-0.5 text-[10px]' : '-top-2.5 px-1.5 py-0.5 text-[9px]',
            badge === 'hot' && 'bg-amber-500 text-white animate-ai-chain-badge-hot',
            badge === 'next' && 'bg-orange-500 text-white animate-ai-chain-badge-next',
            badge === 'theme' && 'bg-emerald-500 text-white animate-ai-chain-badge-theme',
          )}
        >
          {badge === 'hot' ? '当前热点' : badge === 'next' ? '下一站' : '主题切换'}
        </span>
      ) : null}
      <span
        className={cn(
          'relative z-[1] rounded-full shrink-0 ring-[3px] ring-background',
          large ? 'h-4 w-4' : 'h-3.5 w-3.5',
          theme.dot,
          theme.dotRing,
          pulse === 'hot' && 'animate-ai-chain-pulse-hot',
          pulse === 'next' && 'animate-ai-chain-pulse-next',
          pulse === 'theme' && 'animate-ai-chain-pulse-theme',
          skipped && 'opacity-70',
        )}
      />
      <span
        className={cn(
          'leading-tight text-center font-medium',
          large ? 'text-[11px]' : 'text-[10px]',
          skipped ? 'text-muted-foreground line-through decoration-muted-foreground/50' : 'text-foreground/85',
          active && 'text-primary font-semibold',
        )}
      >
        {theme.label}
      </span>
      {skipped ? (
        <span className={cn('text-muted-foreground/80 leading-none', large ? 'text-[9px]' : 'text-[8px]')}>A股跳过</span>
      ) : count > 0 ? (
        <span className={cn('leading-none rounded-md px-1.5 py-px font-medium', large ? 'text-[10px]' : 'text-[9px]', theme.badge)}>
          {count}
        </span>
      ) : (
        <span className={cn('text-transparent leading-none select-none', large ? 'text-[10px]' : 'text-[9px]')}>0</span>
      )}
    </button>
  )
}

function FlowTrack({ className, large = false }: { className?: string; large?: boolean }) {
  return (
    <div
      className={cn(
        'absolute inset-x-0 overflow-hidden rounded-full bg-border/50',
        large ? 'top-[26px] h-1.5' : 'top-[22px] h-1',
        className,
      )}
    >
      <div className="absolute inset-y-0 w-2/5 bg-gradient-to-r from-transparent via-primary/80 to-transparent animate-ai-chain-flow" />
    </div>
  )
}

export function AiChainRotationBanner({
  layerCounts,
  activeFilterKey = '',
  onToggleFilter,
  className,
}: AiChainRotationBannerProps) {
  const handleToggle = (layerKey: string) => {
    onToggleFilter?.(chainLayerFilterKey(layerKey))
  }

  const isActive = (layerKey: string) => activeFilterKey === chainLayerFilterKey(layerKey)

  return (
    <div className={cn('rounded-xl border border-border/50 bg-accent/15 p-3.5 sm:p-4', className)}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="text-[13px] sm:text-sm font-semibold text-foreground/90">AI 行情轮动</div>
        <div className="text-[10px] sm:text-[11px] text-muted-foreground hidden sm:block">
          沿物理连接顺序 · 颜色与下方卡片对应
        </div>
      </div>

      {/* 主链：算力 → 云&大模型 → 软件 → 物理AI */}
      <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 mb-3.5 text-[11px] sm:text-xs">
        <span className="rounded-md px-2.5 py-1 bg-sky-500/12 text-sky-700 font-semibold">AI算力</span>
        <ChevronRight className="w-4 h-4 sm:w-5 sm:h-5 text-muted-foreground/60 shrink-0 animate-ai-chain-arrow" />
        <span className="rounded-md px-2.5 py-1 bg-slate-500/10 text-slate-500 line-through decoration-slate-400/60 font-medium">
          云&大模型
          <span className="no-underline ml-1 text-[9px] sm:text-[10px] opacity-80">跳过</span>
        </span>
        <ChevronRight className="w-4 h-4 text-muted-foreground/40 shrink-0" />
        <span className="rounded-md px-2.5 py-1 bg-slate-500/10 text-slate-500 line-through decoration-slate-400/60 font-medium">
          软件应用
          <span className="no-underline ml-1 text-[9px] sm:text-[10px] opacity-80">跳过</span>
        </span>
        <ChevronRight className="w-4 h-4 sm:w-5 sm:h-5 text-muted-foreground/60 shrink-0 animate-ai-chain-arrow" />
        <span className="rounded-md px-2.5 py-1 bg-emerald-500/12 text-emerald-600 font-semibold animate-ai-chain-pulse-theme inline-flex items-center gap-1">
          物理AI
          <span className="text-[9px] sm:text-[10px] opacity-80 font-medium">登场</span>
        </span>
      </div>

      {/* 算力细分链 */}
      <div className="relative mb-4">
        <div className="text-[10px] sm:text-[11px] text-muted-foreground mb-2 pl-0.5 font-medium">算力细分</div>
        <div className="relative overflow-x-auto scrollbar-none -mx-1 px-1 pb-2 pt-3">
          <FlowTrack large />
          <div className="relative z-[1] flex items-start gap-0 min-w-max pt-2">
            {AI_COMPUTE_STEPS.map((theme, index) => (
              <div key={theme.key} className="flex items-start">
                <RotationNode
                  size="lg"
                  theme={theme}
                  count={countForLayer(theme.key, layerCounts)}
                  active={isActive(theme.key)}
                  pulse={
                    theme.key === CHAIN_HOT_LAYER
                      ? 'hot'
                      : theme.key === CHAIN_NEXT_LAYER
                        ? 'next'
                        : null
                  }
                  badge={
                    theme.key === CHAIN_HOT_LAYER
                      ? 'hot'
                      : theme.key === CHAIN_NEXT_LAYER
                        ? 'next'
                        : undefined
                  }
                  onClick={() => handleToggle(theme.key)}
                />
                {index < AI_COMPUTE_STEPS.length - 1 ? (
                  <ChevronRight className="w-4 h-4 sm:w-5 sm:h-5 text-muted-foreground/40 mt-3 shrink-0" />
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 算力链后：跳过环节 + 物理AI */}
      <div className="flex flex-wrap items-center gap-3 sm:gap-4 pt-3 border-t border-border/30">
        <span className="text-[10px] sm:text-[11px] text-muted-foreground shrink-0 font-medium">A股主线</span>
        {AI_POST_COMPUTE_PHASES.map((theme) => (
          <RotationNode
            key={theme.key}
            size="lg"
            theme={theme}
            count={countForLayer(theme.key, layerCounts)}
            active={isActive(theme.key)}
            pulse={theme.key === CHAIN_THEME_LAYER ? 'theme' : null}
            badge={theme.key === CHAIN_THEME_LAYER ? 'theme' : undefined}
            onClick={() => handleToggle(theme.key)}
          />
        ))}
      </div>
    </div>
  )
}
