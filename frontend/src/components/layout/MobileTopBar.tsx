import { NavLink } from 'react-router-dom'
import { Github, Menu, ScrollText, TrendingUp } from 'lucide-react'
import type { ThemeMode } from '@/hooks/use-theme'
import AccountMenu from '@/components/AccountMenu'

interface MobileTopBarProps {
  version: string
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  onOpenLogs: () => void
  onOpenSelfCheck: () => void
  repoUrl: string
  onOpenNav: () => void
}

const iconBtnCls =
  'flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors'

/** 移动端顶栏（<md）：Logo + 全部导航入口 + 工具；导航分组抽屉由 onOpenNav 打开。 */
export default function MobileTopBar({
  version,
  mode,
  onSetMode,
  onOpenLogs,
  onOpenSelfCheck,
  repoUrl,
  onOpenNav,
}: MobileTopBarProps) {
  return (
    <div className="sticky top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-md md:hidden">
      <div className="flex h-12 items-center justify-between px-3 pt-[max(0.25rem,env(safe-area-inset-top))]">
        <NavLink to="/" className="flex items-center gap-2" aria-label="返回首页">
          <div className="flex h-7 w-7 items-center justify-center rounded-xl bg-primary shadow-sm">
            <TrendingUp className="h-3.5 w-3.5 text-primary-foreground" />
          </div>
          <span className="text-body font-bold tracking-tight text-foreground">PanWatch</span>
          {version && <span className="text-mini text-muted-foreground">v{version}</span>}
        </NavLink>

        <div className="flex items-center gap-0.5">
          <button onClick={onOpenNav} className={iconBtnCls} title="全部导航" aria-label="打开全部导航">
            <Menu className="h-4 w-4" />
          </button>
          <button
            onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
            className={iconBtnCls}
            title="GitHub 项目"
            aria-label="GitHub 项目"
          >
            <Github className="h-4 w-4" />
          </button>
          <button onClick={onOpenLogs} className={iconBtnCls} title="查看日志" aria-label="查看日志">
            <ScrollText className="h-4 w-4" />
          </button>
          <AccountMenu size="sm" mode={mode} onSetMode={onSetMode} onOpenSelfCheck={onOpenSelfCheck} />
        </div>
      </div>
    </div>
  )
}
