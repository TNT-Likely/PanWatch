import { NavLink, useLocation } from 'react-router-dom'
import { Github, PanelLeftClose, PanelLeftOpen, ScrollText, Stethoscope, TrendingUp } from 'lucide-react'
import type { ThemeMode } from '@/hooks/use-theme'
import AccountMenu from '@/components/AccountMenu'
import { preloadRoute } from '@/router/page-loaders'
import { useLocalStorage } from '@/lib/utils'
import { NAV_GROUPS, isNavItemActive } from './nav-config'

interface AppSidebarProps {
  version: string
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  onOpenLogs: () => void
  onOpenSelfCheck: () => void
  repoUrl: string
}

const iconBtnCls =
  'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors'

/**
 * 桌面端可折叠侧边栏（md+）。
 * 全部 11 个页面按「盯盘 / 决策 / 复盘 / 系统」分组一级可达；折叠态持久化。
 */
export default function AppSidebar({ version, mode, onSetMode, onOpenLogs, onOpenSelfCheck, repoUrl }: AppSidebarProps) {
  const [collapsed, setCollapsed] = useLocalStorage('panwatch_sidebar_collapsed', false)
  const location = useLocation()

  const tools = (
    <>
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
      <button onClick={onOpenSelfCheck} className={iconBtnCls} title="系统自检" aria-label="系统自检">
        <Stethoscope className="h-4 w-4" />
      </button>
      <button
        onClick={() => setCollapsed(v => !v)}
        className={iconBtnCls}
        title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
      >
        {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
      </button>
    </>
  )

  return (
    <aside
      className={`sticky top-0 z-40 hidden h-dvh shrink-0 flex-col border-r border-border/60 bg-card/40 backdrop-blur-sm transition-[width] duration-200 md:flex ${
        collapsed ? 'w-16' : 'w-56'
      }`}
    >
      {/* Logo */}
      <NavLink to="/" className="flex h-14 shrink-0 items-center gap-2.5 px-3" aria-label="返回首页">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary shadow-sm">
          <TrendingUp className="h-4 w-4 text-primary-foreground" />
        </div>
        {!collapsed && (
          <span className="flex items-baseline gap-1.5 overflow-hidden">
            <span className="text-body font-bold tracking-tight text-foreground">PanWatch</span>
            {version && <span className="text-mini text-muted-foreground">v{version}</span>}
          </span>
        )}
      </NavLink>

      {/* 分组导航 */}
      <nav className="scrollbar-none flex-1 overflow-y-auto px-2 pb-3" aria-label="主导航">
        {NAV_GROUPS.map(group => (
          <div key={group.key}>
            {collapsed ? (
              <div className="mx-2 my-2 h-px bg-border/50" role="presentation" />
            ) : (
              <div className="px-2.5 pb-1 pt-3 text-mini font-medium text-muted-foreground">{group.label}</div>
            )}
            <div className="space-y-0.5">
              {group.items.map(({ to, icon: Icon, label }) => {
                const active = isNavItemActive(location.pathname, to)
                return (
                  <NavLink
                    key={to}
                    to={to}
                    title={collapsed ? label : undefined}
                    aria-current={active ? 'page' : undefined}
                    aria-label={label}
                    onMouseEnter={() => preloadRoute(to)}
                    onFocus={() => preloadRoute(to)}
                    className={`flex items-center gap-2.5 rounded-lg text-body font-medium transition-colors ${
                      collapsed ? 'justify-center px-0 py-2.5' : 'px-2.5 py-2'
                    } ${active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'}`}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {!collapsed && label}
                  </NavLink>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* 底部工具行 */}
      <div
        className={`shrink-0 border-t border-border/60 p-2 ${collapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-1'}`}
      >
        <AccountMenu size="sm" mode={mode} onSetMode={onSetMode} onOpenSelfCheck={onOpenSelfCheck} />
        {tools}
      </div>
    </aside>
  )
}
