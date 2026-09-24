import { NavLink, useLocation } from 'react-router-dom'
import { preloadRoute } from '@/router/page-loaders'
import { MOBILE_TAB_ITEMS, isNavItemActive } from './nav-config'

/** 移动端底部 Tab（<md）：5 个高频页面直达，触控区 ≥44px。 */
export default function MobileTabBar() {
  const location = useLocation()

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-40 border-t border-border/60 bg-background/90 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
      aria-label="移动端主导航"
    >
      <div className="flex h-14 items-center justify-around">
        {MOBILE_TAB_ITEMS.map(({ to, icon: Icon, label }) => {
          const active = isNavItemActive(location.pathname, to)
          return (
            <NavLink
              key={to}
              to={to}
              aria-current={active ? 'page' : undefined}
              aria-label={label}
              onMouseEnter={() => preloadRoute(to)}
              onFocus={() => preloadRoute(to)}
              className={`flex min-h-[44px] min-w-[56px] flex-col items-center justify-center gap-0.5 rounded-xl px-2 py-1.5 transition-colors ${
                active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent/40'
              }`}
            >
              <Icon className="h-5 w-5" />
              <span className="text-mini font-medium">{label}</span>
            </NavLink>
          )
        })}
      </div>
    </nav>
  )
}
