import { useLocation, NavLink } from 'react-router-dom'
import { LogOut, Monitor, Moon, Stethoscope, Sun, type LucideIcon } from 'lucide-react'
import { isAuthenticated, logout } from '@panwatch/api'
import type { ThemeMode } from '@/hooks/use-theme'
import { useStockColorMode, setStockColorMode } from '@panwatch/base-ui/hooks/use-stock-mode'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { NAV_GROUPS, isNavItemActive } from './nav-config'

interface MobileNavDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  onOpenSelfCheck: () => void
}

const THEME_OPTIONS: { value: ThemeMode; icon: LucideIcon; label: string }[] = [
  { value: 'light', icon: Sun, label: '亮色' },
  { value: 'dark', icon: Moon, label: '暗色' },
  { value: 'system', icon: Monitor, label: '跟随系统' },
]

/**
 * 移动端「全部导航」分组抽屉：与桌面侧边栏同一数据源（NAV_GROUPS），
 * 并收纳主题切换 / 系统自检 / 退出登录。
 */
export default function MobileNavDrawer({ open, onOpenChange, mode, onSetMode, onOpenSelfCheck }: MobileNavDrawerProps) {
  const location = useLocation()
  const stockMode = useStockColorMode()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xs gap-3">
        <DialogHeader>
          <DialogTitle>全部导航</DialogTitle>
          <DialogDescription>按「盯盘 / 决策 / 复盘 / 系统」分组</DialogDescription>
        </DialogHeader>

        <nav className="scrollbar -mx-1 max-h-[52vh] overflow-y-auto px-1" aria-label="全部页面">
          {NAV_GROUPS.map(group => (
            <div key={group.key} className="mb-3">
              <div className="px-1 pb-1 text-mini font-medium text-muted-foreground">{group.label}</div>
              <div className="grid grid-cols-2 gap-1">
                {group.items.map(({ to, icon: Icon, label }) => {
                  const active = isNavItemActive(location.pathname, to)
                  return (
                    <NavLink
                      key={to}
                      to={to}
                      onClick={() => onOpenChange(false)}
                      aria-current={active ? 'page' : undefined}
                      className={`flex min-h-[44px] items-center gap-2 rounded-lg px-2.5 text-body font-medium transition-colors ${
                        active
                          ? 'bg-primary/10 text-primary'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                      }`}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {label}
                    </NavLink>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="h-px bg-border/50" role="presentation" />

        <div className="flex items-center gap-1">
          {THEME_OPTIONS.map(({ value, icon: Icon, label }) => {
            const active = mode === value
            return (
              <button
                key={value}
                onClick={() => onSetMode(value)}
                title={label}
                aria-label={`主题：${label}`}
                aria-pressed={active}
                className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors ${
                  active ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="h-4 w-4" />
              </button>
            )
          })}
          <span className="flex-1" role="presentation" />
          <button
            onClick={() => setStockColorMode(stockMode === 'up-red' ? 'up-green' : 'up-red')}
            title={stockMode === 'up-red' ? '当前：红涨绿跌（点击切换）' : '当前：绿涨红跌（点击切换）'}
            aria-label={`涨跌颜色：${stockMode === 'up-red' ? '红涨绿跌' : '绿涨红跌'}，点击切换`}
            className="flex h-9 items-center gap-1 rounded-lg px-2 text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <span className="font-mono text-body-sm">
              <span className={stockMode === 'up-red' ? 'text-stock-up' : 'text-stock-down'}>▲</span>
              <span className={stockMode === 'up-red' ? 'text-stock-down' : 'text-stock-up'}>▼</span>
            </span>
          </button>
          <button
            onClick={() => {
              onOpenChange(false)
              onOpenSelfCheck()
            }}
            title="系统自检"
            aria-label="系统自检"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <Stethoscope className="h-4 w-4" />
          </button>
          {isAuthenticated() && (
            <button
              onClick={logout}
              title="退出登录"
              aria-label="退出登录"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
            >
              <LogOut className="h-4 w-4" />
            </button>
          )}
        </div>
        {/* 屏幕阅读器可用性：当前主题状态文字提示 */}
        <span className="sr-only" aria-live="polite">
          {THEME_OPTIONS.filter(o => o.value === mode)
            .map(o => `当前主题：${o.label}`)
            .join('')}
        </span>
      </DialogContent>
    </Dialog>
  )
}
