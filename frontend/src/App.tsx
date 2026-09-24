import { Suspense, useState, useEffect, useRef } from 'react'
import { Routes, Route, useLocation, Navigate } from 'react-router-dom'
import { useTheme } from '@/hooks/use-theme'
import { appApi } from '@panwatch/api/app'
import { fetchAPI, isAuthenticated } from '@panwatch/api/client'
import LogsModal from '@panwatch/biz-ui/components/logs-modal'
import AmbientBackground from '@panwatch/biz-ui/components/AmbientBackground'
import AssistantOpenBridge from '@/components/AssistantOpenBridge'
import SelfCheckModal from '@/components/SelfCheckModal'
import { RouteErrorBoundary, RouteLoadingFallback } from '@/components/RouteBoundary'
import { routePages } from '@/router/page-loaders'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import AppSidebar from '@/components/layout/AppSidebar'
import MobileTopBar from '@/components/layout/MobileTopBar'
import MobileTabBar from '@/components/layout/MobileTabBar'
import MobileNavDrawer from '@/components/layout/MobileNavDrawer'

const {
  LoginPage,
  DashboardPage,
  OpportunitiesPage,
  StocksPage,
  AgentsPage,
  SettingsPage,
  DataSourcesPage,
  HistoryPage,
  AnalysisDetailPage,
  PriceAlertsPage,
  PaperTradingPage,
  EvaluationsPage,
  AssistantPage,
} = routePages

const REPO_URL = 'https://github.com/TNT-Likely/PanWatch'

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

function App() {
  const { mode, setMode } = useTheme()
  const location = useLocation()
  const isAssistantRoute = location.pathname === '/assistant' || location.pathname.startsWith('/assistant/')
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [selfCheckOpen, setSelfCheckOpen] = useState(false)
  const [navDrawerOpen, setNavDrawerOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const checkedUpdateRef = useRef(false)

  useEffect(() => {
    appApi.version()
      .then(data => setVersion(data?.version || ''))
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

  // 登录页面不显示导航
  if (location.pathname === '/login') {
    return (
      <RouteErrorBoundary>
        <Suspense fallback={<RouteLoadingFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </Suspense>
      </RouteErrorBoundary>
    )
  }

  return (
    <RequireAuth>
      <div
        className={`relative bg-background ${
          isAssistantRoute ? 'flex h-dvh flex-col overflow-hidden pb-14 md:pb-0' : 'min-h-dvh pb-14 md:pb-0'
        }`}
      >
        <AmbientBackground />

        <div className="flex min-h-dvh">
          {/* 桌面侧边栏（md+，可折叠，全部页面分组可达） */}
          <AppSidebar
            version={version}
            mode={mode}
            onSetMode={setMode}
            onOpenLogs={() => setLogsOpen(true)}
            onOpenSelfCheck={() => setSelfCheckOpen(true)}
            repoUrl={REPO_URL}
          />

          <div className="flex min-w-0 flex-1 flex-col">
            {/* 移动端顶栏（<md） */}
            <MobileTopBar
              version={version}
              mode={mode}
              onSetMode={setMode}
              onOpenLogs={() => setLogsOpen(true)}
              onOpenSelfCheck={() => setSelfCheckOpen(true)}
              repoUrl={REPO_URL}
              onOpenNav={() => setNavDrawerOpen(true)}
            />

            {/* Content */}
            <main
              className={`${
                isAssistantRoute
                  ? 'flex min-h-0 flex-1 flex-col overflow-hidden'
                  : 'mx-auto w-full max-w-[1680px] flex-1 px-4 py-4 md:px-6 md:py-6 lg:px-8'
              }`}
            >
              <AssistantOpenBridge />
              <RouteErrorBoundary>
                <Suspense fallback={<RouteLoadingFallback />}>
                  <Routes>
                    <Route path="/" element={<DashboardPage />} />
                    <Route path="/opportunities" element={<OpportunitiesPage />} />
                    <Route path="/portfolio" element={<StocksPage />} />
                    <Route path="/agents" element={<AgentsPage />} />
                    <Route path="/evaluations" element={<EvaluationsPage />} />
                    <Route path="/history" element={<HistoryPage />} />
                    <Route path="/paper-trading" element={<PaperTradingPage />} />
                    <Route path="/alerts" element={<PriceAlertsPage />} />
                    <Route path="/assistant" element={<AssistantPage />} />
                    <Route path="/assistant/:conversationId" element={<AssistantPage />} />
                    <Route path="/datasources" element={<DataSourcesPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />
                  </Routes>
                </Suspense>
              </RouteErrorBoundary>
            </main>
          </div>
        </div>

        <MobileTabBar />
        <MobileNavDrawer
          open={navDrawerOpen}
          onOpenChange={setNavDrawerOpen}
          mode={mode}
          onSetMode={setMode}
          onOpenSelfCheck={() => setSelfCheckOpen(true)}
        />

        <LogsModal open={logsOpen} onOpenChange={setLogsOpen} />
        <SelfCheckModal open={selfCheckOpen} onClose={() => setSelfCheckOpen(false)} />
        <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>发现新版本</DialogTitle>
              <DialogDescription>
                当前版本 v{version}，可升级到 v{upgradeInfo?.latest}。
              </DialogDescription>
            </DialogHeader>
            <div className="text-body text-muted-foreground">
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
