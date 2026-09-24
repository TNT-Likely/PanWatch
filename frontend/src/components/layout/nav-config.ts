import {
  Activity,
  BellRing,
  Bot,
  ClipboardCheck,
  Clock,
  Database,
  LayoutDashboard,
  List,
  MessageCircle,
  Settings,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  icon: LucideIcon
  label: string
}

export interface NavGroup {
  key: string
  label: string
  items: NavItem[]
}

/**
 * 全站导航唯一数据源：桌面侧边栏 / 移动端抽屉共用。
 * 分组与「盘前盯盘 → 决策执行 → 盘后复盘 → 系统支撑」的用户旅程对齐。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'watch',
    label: '盯盘',
    items: [
      { to: '/', icon: LayoutDashboard, label: '首页' },
      { to: '/portfolio', icon: List, label: '持仓' },
      { to: '/opportunities', icon: Sparkles, label: '机会' },
      { to: '/alerts', icon: BellRing, label: '提醒' },
    ],
  },
  {
    key: 'decision',
    label: '决策',
    items: [
      { to: '/paper-trading', icon: Activity, label: '模拟盘' },
      { to: '/assistant', icon: MessageCircle, label: '助手' },
      { to: '/agents', icon: Bot, label: 'Agent' },
    ],
  },
  {
    key: 'review',
    label: '复盘',
    items: [
      { to: '/evaluations', icon: ClipboardCheck, label: '验证中心' },
      { to: '/history', icon: Clock, label: '历史' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    items: [
      { to: '/datasources', icon: Database, label: '数据源' },
      { to: '/settings', icon: Settings, label: '设置' },
    ],
  },
]

/** 移动端底部 Tab：保持既有 5 项使用习惯，其余页面经「更多」抽屉可达。 */
export const MOBILE_TAB_ITEMS: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: '首页' },
  { to: '/portfolio', icon: List, label: '持仓' },
  { to: '/opportunities', icon: Sparkles, label: '机会' },
  { to: '/paper-trading', icon: Activity, label: '模拟盘' },
  { to: '/assistant', icon: MessageCircle, label: '助手' },
]

export function isNavItemActive(pathname: string, to: string): boolean {
  return to === '/' ? pathname === '/' : pathname.startsWith(to)
}
