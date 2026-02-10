import { useState, useEffect, useRef } from 'react'
import { Routes, Route, NavLink, useLocation, Navigate } from 'react-router-dom'
import { Moon, Sun, TrendingUp, Bot, ScrollText, Settings, List, Database, Clock, LayoutDashboard, LogOut, Github, BellRing, MoreHorizontal } from 'lucide-react'
import { useTheme } from '@/hooks/use-theme'
import { fetchAPI, isAuthenticated, logout } from '@/lib/utils'
import DashboardPage from '@/pages/Dashboard'
import StocksPage from '@/pages/Stocks'
import AgentsPage from '@/pages/Agents'
import SettingsPage from '@/pages/Settings'
import DataSourcesPage from '@/pages/DataSources'
import HistoryPage from '@/pages/History'
import PriceAlertsPage from '@/pages/PriceAlerts'
import LoginPage from '@/pages/Login'
import LogsModal from '@/components/logs-modal'
import AmbientBackground from '@/components/AmbientBackground'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/portfolio', icon: List, label: '持仓' },
  { to: '/agents', icon: Bot, label: 'Agent' },
  { to: '/history', icon: Clock, label: '历史' },
  { to: '/alerts', icon: BellRing, label: '提醒' },
  { to: '/datasources', icon: Database, label: '数据源' },
  { to: '/settings', icon: Settings, label: '设置' },
]
const desktopPrimaryNavItems = [navItems[0], navItems[1], navItems[2], navItems[4]]
const desktopMoreNavItems = [navItems[3], navItems[5], navItems[6]]
const mobilePrimaryNavItems = [navItems[0], navItems[1], navItems[2], navItems[4]]
const mobileMoreNavItems = [navItems[3], navItems[5], navItems[6]]

// 认证守卫组件
function RequireAuth({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<'checking' | 'authenticated' | 'unauthenticated'>('checking')
  const location = useLocation()

  useEffect(() => {
    // 检查本地 token
    if (isAuthenticated()) {
      setAuthState('authenticated')
      return
    }

    // 没有 token，需要去登录页（设置密码或登录）
    setAuthState('unauthenticated')
  }, [])

  if (authState === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}

const API_BASE = import.meta.env.VITE_API_BASE || ''

function App() {
  const { theme, toggleTheme } = useTheme()
  const location = useLocation()
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const [desktopMoreOpen, setDesktopMoreOpen] = useState(false)
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false)
  const checkedUpdateRef = useRef(false)
  const desktopMoreRef = useRef<HTMLDivElement | null>(null)
  const mobileMoreRef = useRef<HTMLDivElement | null>(null)
  const repoUrl = 'https://github.com/TNT-Likely/PanWatch'

  useEffect(() => {
    fetch(`${API_BASE}/api/version`)
      .then(res => res.json())
      .then(data => setVersion(data.data?.version || data.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (checkedUpdateRef.current) return
    if (!isAuthenticated()) return
    const current = String(version || '').trim()
    if (!current || current === 'dev') return
    checkedUpdateRef.current = true

    fetchAPI<any>('/settings/update-check')
      .then((res) => {
        const latest = String(res?.latest_version || '').trim()
        const shouldOpen = !!res?.update_available && !!latest
        if (!shouldOpen) return
        const dismissed = localStorage.getItem('panwatch_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || 'https://github.com/sunxiao0721/PanWatch/releases') })
        setUpgradeOpen(true)
      })
      .catch(() => {})
  }, [version])

  useEffect(() => {
    const onDocPointerDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (desktopMoreOpen && desktopMoreRef.current && !desktopMoreRef.current.contains(t)) {
        setDesktopMoreOpen(false)
      }
      if (mobileMoreOpen && mobileMoreRef.current && !mobileMoreRef.current.contains(t)) {
        setMobileMoreOpen(false)
      }
    }
    document.addEventListener('pointerdown', onDocPointerDown)
    return () => document.removeEventListener('pointerdown', onDocPointerDown)
  }, [desktopMoreOpen, mobileMoreOpen])

  useEffect(() => {
    setDesktopMoreOpen(false)
    setMobileMoreOpen(false)
  }, [location.pathname])

  // 登录页面不显示导航
  if (location.pathname === '/login') {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
      </Routes>
    )
  }

  return (
    <RequireAuth>
    <div className="min-h-screen pb-16 md:pb-0 relative overflow-x-hidden bg-background">
      <AmbientBackground />
      {/* Desktop Floating Nav */}
      <div className="sticky top-0 z-50 px-4 md:px-6 pt-3 md:pt-4 pb-2 hidden md:block">
        <header className="card px-4 md:px-5">
          <div className="h-14 flex items-center justify-between">
            {/* Logo */}
            <NavLink to="/" className="flex items-center gap-2.5 group">
              <div className="w-8 h-8 rounded-2xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-4 h-4 text-white" />
              </div>
              <span className="text-[15px] font-bold text-foreground">PanWatch</span>
              {version && <span className="text-[11px] text-muted-foreground/60 font-normal">v{version}</span>}
            </NavLink>

            {/* Nav Links */}
            <nav className="flex items-center gap-1">
              {desktopPrimaryNavItems.map(({ to, icon: Icon, label }) => {
                const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
                return (
                  <NavLink
                    key={to}
                    to={to}
                    className="relative"
                  >
                    <span
                      className={`absolute inset-0 rounded-xl transition-all ${
                        isActive
                          ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--success)/0.06))] ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                          : 'bg-transparent'
                      }`}
                    />
                    <span
                      className={`relative px-3.5 py-2 rounded-xl text-[13px] font-medium transition-all flex items-center gap-1.5 ${
                        isActive
                          ? 'text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${isActive ? 'text-primary' : ''}`} />
                      {label}
                    </span>
                  </NavLink>
                )
              })}
              <div className="relative" ref={desktopMoreRef}>
                <button
                  onClick={() => setDesktopMoreOpen(v => !v)}
                  className={`relative px-3.5 py-2 rounded-xl text-[13px] font-medium transition-all flex items-center gap-1.5 ${
                    desktopMoreNavItems.some(item => location.pathname.startsWith(item.to))
                      ? 'text-foreground bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--success)/0.06))] ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                >
                  <MoreHorizontal className="w-4 h-4" />
                  更多
                </button>
                {desktopMoreOpen && (
                  <div className="absolute right-0 mt-2 w-40 rounded-xl border border-border/60 bg-card/95 backdrop-blur p-1.5 shadow-xl">
                    {desktopMoreNavItems.map(({ to, icon: Icon, label }) => {
                      const isActive = location.pathname.startsWith(to)
                      return (
                        <NavLink
                          key={to}
                          to={to}
                          onClick={() => setDesktopMoreOpen(false)}
                          className={`flex items-center gap-2 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                            isActive ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                          }`}
                        >
                          <Icon className="w-3.5 h-3.5" />
                          {label}
                        </NavLink>
                      )
                    })}
                  </div>
                )}
              </div>
            </nav>

            {/* Theme Toggle & Logout */}
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="GitHub 项目"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="查看日志"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <button
                onClick={toggleTheme}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title={theme === 'dark' ? '切换到亮色' : '切换到暗色'}
              >
                {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              </button>
              {isAuthenticated() && (
                <button
                  onClick={logout}
                  className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all"
                  title="退出登录"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </header>
      </div>

      {/* Mobile Top Bar */}
      <div className="sticky top-0 z-50 px-4 pt-3 pb-2 md:hidden">
        <header className="card px-4">
          <div className="h-12 flex items-center justify-between">
            <NavLink to="/" className="flex items-center gap-2 group">
              <div className="w-7 h-7 rounded-xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-3.5 h-3.5 text-white" />
              </div>
              <span className="text-[14px] font-bold text-foreground">PanWatch</span>
              {version && <span className="text-[10px] text-muted-foreground/60 font-normal">v{version}</span>}
            </NavLink>
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="GitHub 项目"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="查看日志"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <button
                onClick={toggleTheme}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title={theme === 'dark' ? '切换到亮色' : '切换到暗色'}
              >
                {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              </button>
            </div>
          </div>
        </header>
      </div>

      {/* Mobile Bottom Nav */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-card border-t border-border px-2 pb-[env(safe-area-inset-bottom)]" ref={mobileMoreRef}>
        <div className="flex items-center justify-around h-14">
          {mobilePrimaryNavItems.map(({ to, icon: Icon, label }) => {
            const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                className={`flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 rounded-xl transition-all min-w-[56px] ${
                  isActive
                    ? 'text-primary bg-primary/8 ring-1 ring-primary/15'
                    : 'text-muted-foreground hover:bg-accent/30'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-[10px] font-medium">{label}</span>
              </NavLink>
            )
          })}
          <button
            onClick={() => setMobileMoreOpen(v => !v)}
            className={`flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 rounded-xl transition-all min-w-[56px] ${
              mobileMoreNavItems.some(item => location.pathname.startsWith(item.to))
                ? 'text-primary bg-primary/8 ring-1 ring-primary/15'
                : 'text-muted-foreground hover:bg-accent/30'
            }`}
          >
            <MoreHorizontal className="w-5 h-5" />
            <span className="text-[10px] font-medium">更多</span>
          </button>
        </div>
        {mobileMoreOpen && (
          <div className="absolute bottom-[58px] right-2 w-40 rounded-xl border border-border/60 bg-card/95 backdrop-blur p-1.5 shadow-xl">
            {mobileMoreNavItems.map(({ to, icon: Icon, label }) => {
              const isActive = location.pathname.startsWith(to)
              return (
                <NavLink
                  key={to}
                  to={to}
                  onClick={() => setMobileMoreOpen(false)}
                  className={`flex items-center gap-2 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                    isActive ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {label}
                </NavLink>
              )
            })}
          </div>
        )}
      </nav>

      {/* Content */}
      <main className="px-4 md:px-6 py-4 md:py-6 w-full">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/portfolio" element={<StocksPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/alerts" element={<PriceAlertsPage />} />
          <Route path="/datasources" element={<DataSourcesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      <LogsModal open={logsOpen} onOpenChange={setLogsOpen} />
      <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>发现新版本</DialogTitle>
            <DialogDescription>
              当前版本 v{version}，可升级到 v{upgradeInfo?.latest}。
            </DialogDescription>
          </DialogHeader>
          <div className="text-[12px] text-muted-foreground">
            建议升级以获取最新功能和修复。
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                if (upgradeInfo?.latest) localStorage.setItem('panwatch_upgrade_dismissed_version', upgradeInfo.latest)
                setUpgradeOpen(false)
              }}
            >
              稍后提醒
            </Button>
            <Button
              onClick={() => {
                const url = upgradeInfo?.url || 'https://github.com/sunxiao0721/PanWatch/releases'
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
            >
              去升级
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
    </RequireAuth>
  )
}

export default App
