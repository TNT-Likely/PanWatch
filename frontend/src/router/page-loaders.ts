import { lazy } from 'react'

export const pageLoaders = {
  login: () => import('@/pages/Login'),
  dashboard: () => import('@/pages/Dashboard'),
  stocks: () => import('@/pages/Stocks'),
  opportunities: () => import('@/pages/Opportunities'),
  paperTrading: () => import('@/pages/PaperTrading'),
  assistant: () => import('@/pages/Assistant'),
  alerts: () => import('@/pages/PriceAlerts'),
  agents: () => import('@/pages/Agents'),
  evaluations: () => import('@/pages/Evaluations'),
  history: () => import('@/pages/History'),
  dataSources: () => import('@/pages/DataSources'),
  settings: () => import('@/pages/Settings'),
  analysisDetail: () => import('@/pages/AnalysisDetail'),
} as const

export type RouteKey = keyof typeof pageLoaders

export const routePages = {
  LoginPage: lazy(pageLoaders.login),
  DashboardPage: lazy(pageLoaders.dashboard),
  StocksPage: lazy(pageLoaders.stocks),
  OpportunitiesPage: lazy(pageLoaders.opportunities),
  PaperTradingPage: lazy(pageLoaders.paperTrading),
  AssistantPage: lazy(pageLoaders.assistant),
  PriceAlertsPage: lazy(pageLoaders.alerts),
  AgentsPage: lazy(pageLoaders.agents),
  EvaluationsPage: lazy(pageLoaders.evaluations),
  HistoryPage: lazy(pageLoaders.history),
  DataSourcesPage: lazy(pageLoaders.dataSources),
  SettingsPage: lazy(pageLoaders.settings),
  AnalysisDetailPage: lazy(pageLoaders.analysisDetail),
} as const

const routeMatchers: Array<{ key: RouteKey; matches: (pathname: string) => boolean }> = [
  { key: 'login', matches: pathname => pathname === '/login' },
  { key: 'dashboard', matches: pathname => pathname === '/' },
  { key: 'stocks', matches: pathname => pathname === '/portfolio' },
  { key: 'opportunities', matches: pathname => pathname === '/opportunities' },
  { key: 'paperTrading', matches: pathname => pathname === '/paper-trading' },
  { key: 'assistant', matches: pathname => pathname === '/assistant' || pathname.startsWith('/assistant/') },
  { key: 'alerts', matches: pathname => pathname === '/alerts' },
  { key: 'agents', matches: pathname => pathname === '/agents' },
  { key: 'evaluations', matches: pathname => pathname === '/evaluations' },
  { key: 'history', matches: pathname => pathname === '/history' },
  { key: 'dataSources', matches: pathname => pathname === '/datasources' },
  { key: 'settings', matches: pathname => pathname === '/settings' },
  { key: 'analysisDetail', matches: pathname => pathname.startsWith('/analysis/') },
]

export function resolveRouteKey(pathname: string): RouteKey | null {
  return routeMatchers.find(({ matches }) => matches(pathname))?.key || null
}

export function preloadRoute(pathname: string): void {
  const key = resolveRouteKey(pathname)
  if (!key) return
  void pageLoaders[key]().catch(() => {})
}
