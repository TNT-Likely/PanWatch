import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Moon, Sun, Monitor, LogOut, Stethoscope, type LucideIcon } from 'lucide-react'
import { isAuthenticated, logout } from '@panwatch/api'
import type { ThemeMode } from '@/hooks/use-theme'
import { useAvatar } from '@/hooks/use-avatar'
import { useTranslation } from 'react-i18next'
import { changeLocale, normalizeLocale, type SupportedLocale } from '@/i18n'
import { useInterfaceLanguage } from '@/i18n/interface-language'
import { UserAvatar } from '@/components/UserAvatar'

export interface AccountNavItem {
  to: string
  icon: LucideIcon
  label: string
}

const THEME_OPTIONS: { value: ThemeMode; icon: LucideIcon; labelKey: 'light' | 'dark' | 'system' }[] = [
  { value: 'light', icon: Sun, labelKey: 'light' },
  { value: 'dark', icon: Moon, labelKey: 'dark' },
  { value: 'system', icon: Monitor, labelKey: 'system' },
]

const LANGUAGE_OPTIONS: { value: SupportedLocale; labelKey: 'simplifiedChinese' | 'english' }[] = [
  { value: 'zh-CN', labelKey: 'simplifiedChinese' },
  { value: 'en-US', labelKey: 'english' },
]

const preferenceOption = 'flex h-6 min-w-0 flex-1 items-center justify-center rounded-md px-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary aria-pressed:bg-primary/10 aria-pressed:text-primary [@media(pointer:coarse)]:h-8'
const preferenceRow = 'col-span-2 grid grid-cols-[subgrid] items-center'

interface AccountMenuProps {
  /** 原“更多”里折叠的导航项(Agent / 历史 / 数据源 / 设置)。 */
  navItems: AccountNavItem[]
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  /** 打开「系统自检」弹窗(状态由上层 App 托管,避免桌面/移动两个实例重复)。 */
  onOpenSelfCheck: () => void
  /** 头像尺寸:桌面 md,移动端 sm。 */
  size?: 'sm' | 'md'
}

/**
 * 右上角头像区域 + 下拉菜单(参考 beecount-cloud):
 * 把原“更多”导航、主题色(亮/暗/跟随系统)、退出登录收进头像下拉
 * (查看日志 / GitHub 仍在外侧)。
 */
export default function AccountMenu({
  navItems,
  mode,
  onSetMode,
  onOpenSelfCheck,
  size = 'md',
}: AccountMenuProps) {
  const { t, i18n: i18nInstance } = useTranslation('settings')
  const currentLocale = normalizeLocale(i18nInstance.resolvedLanguage || i18nInstance.language)
  useInterfaceLanguage()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)
  const location = useLocation()
  const avatar = useAvatar()
  // 仅在支持 hover 的设备(PC)启用悬停展开;触屏维持点击
  const [canHover] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(hover: hover)').matches,
  )

  // 点击外部关闭
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // 路由变化时关闭
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  const avatarSize = size === 'sm' ? 'w-6 h-6' : 'w-7 h-7'

  return (
    <div
      className="relative"
      ref={ref}
      onMouseEnter={canHover ? () => setOpen(true) : undefined}
      onMouseLeave={canHover ? () => setOpen(false) : undefined}
      onKeyDown={event => {
        if (event.key !== 'Escape' || !open) return
        event.stopPropagation()
        setOpen(false)
        trigger.current?.focus()
      }}
    >
      <button
        type="button"
        ref={trigger}
        onClick={event => setOpen(value => canHover && event.detail > 0 ? true : !value)}
        className={`${avatarSize} rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm ring-1 transition-all ${
          open ? 'ring-primary/50' : 'ring-border/40 hover:ring-primary/40'
        }`}
        title={t('account.menuTitle')}
        aria-label={t('account.menuTitle')}
        aria-expanded={open}
      >
        <UserAvatar src={avatar} alt={t('account.avatarAlt')} />
      </button>

      {open && (
        // top-full + pt-2:用透明内边距桥接头像与菜单,hover 移入不断开
        <div className="absolute right-0 top-full pt-2 z-50">
          <div className="scrollbar max-h-[calc(100dvh-8rem-env(safe-area-inset-top)-env(safe-area-inset-bottom))] w-56 max-w-[calc(100vw-3.5rem)] overflow-y-auto overscroll-contain rounded-xl border border-border/60 bg-card/95 p-1.5 shadow-xl backdrop-blur md:max-h-[calc(100dvh-5rem)]">
          {/* 原“更多”导航 */}
          {navItems.map(({ to, icon: Icon, label }) => {
            const isActive = location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                onClick={() => setOpen(false)}
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  isActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            )
          })}

          <div className="my-1 h-px bg-border/50" />

          <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-2 gap-y-1 px-2 py-1">
            <div className={preferenceRow} role="group" aria-label={t('account.themeTitle')}>
              <p className="text-[11px] text-muted-foreground">{t('account.themeTitle')}</p>
              <div className="flex gap-0.5 rounded-lg bg-accent/30 p-0.5">
                {THEME_OPTIONS.map(({ value, icon: Icon, labelKey }) => {
                  const label = t(`account.theme.${labelKey}`)
                  return <button key={value} type="button" title={label} aria-label={label} aria-pressed={mode === value}
                    className={preferenceOption} onClick={() => onSetMode(value)}>
                    <Icon aria-hidden="true" className="h-4 w-4" />
                  </button>
                })}
              </div>
            </div>
            <div className={preferenceRow} role="group" aria-label={t('language.title')}>
              <p className="text-[11px] text-muted-foreground">{t('language.menuLabel')}</p>
              <div className="flex gap-0.5 rounded-lg bg-accent/30 p-0.5">
                {LANGUAGE_OPTIONS.map(({ value, labelKey }) => (
                  <button key={value} type="button" lang={value} aria-pressed={currentLocale === value}
                    className={`${preferenceOption} whitespace-nowrap`} onClick={() => void changeLocale(value)}>
                    {t(`language.${labelKey}`)}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="my-1 h-px bg-border/50" />
          {/* 系统自检:打开弹窗(逐项检查数据源/AI/通知连通性) */}
          <button
            onClick={() => {
              setOpen(false)
              onOpenSelfCheck()
            }}
            className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <Stethoscope className="w-3.5 h-3.5" />
            {t('account.selfCheck')}
          </button>

          {isAuthenticated() && (
            <>
              <div className="my-1 h-px bg-border/50" />
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" />
                {t('account.signOut')}
              </button>
            </>
          )}
          </div>
        </div>
      )}
    </div>
  )
}
