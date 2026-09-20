import { Component, type ErrorInfo, type ReactNode } from 'react'

interface RouteErrorBoundaryProps {
  children: ReactNode
}

interface RouteErrorBoundaryState {
  error: Error | null
}

export function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[320px] items-center justify-center rounded-2xl border border-border/40 bg-card/30">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
        页面加载中…
      </div>
    </div>
  )
}

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('页面模块加载失败:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-2xl border border-destructive/20 bg-card/30 px-6 text-center">
          <p className="text-sm font-medium text-foreground">页面加载失败</p>
          <p className="max-w-md text-xs text-muted-foreground">请重试；如果问题持续存在，可能是浏览器缓存了旧版本页面。</p>
          <button
            type="button"
            className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
