import { ArrowLeft, Loader2, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'

export interface AssistantStockSearchResult {
  symbol: string
  name: string
  market: string
}

interface AssistantStockPickerProps {
  onSelect: (stock: AssistantStockSearchResult) => void
  onCancel: () => void
  disabled?: boolean
}

const MARKETS = [
  { value: 'CN', label: 'A 股' },
  { value: 'HK', label: '港股' },
  { value: 'US', label: '美股' },
]

export function AssistantStockPicker({ onSelect, onCancel, disabled = false }: AssistantStockPickerProps) {
  const [market, setMarket] = useState('CN')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<AssistantStockSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const trimmedQuery = query.trim()
    if (!trimmedQuery) {
      setResults([])
      setLoading(false)
      setError('')
      return
    }

    const controller = new AbortController()
    setResults([])
    const timer = window.setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const nextResults = await fetchAPI<AssistantStockSearchResult[]>(
          `/stocks/search?q=${encodeURIComponent(trimmedQuery)}&market=${market}`,
          { signal: controller.signal },
        )
        if (!controller.signal.aborted) setResults(nextResults)
      } catch (cause) {
        if (!controller.signal.aborted) {
          setResults([])
          setError(cause instanceof Error ? cause.message : '搜索股票失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }, 240)

    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [market, query])

  return (
    <div data-testid="assistant-stock-picker" className="mt-6 w-full max-w-3xl rounded-2xl border border-border/70 bg-background p-3 text-left shadow-[0_18px_50px_-32px_hsl(var(--foreground)/0.5)] sm:p-4">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={disabled}
          className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground disabled:opacity-50"
          aria-label="返回研究入口"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold text-foreground">选择要分析的股票</h2>
          <p className="mt-0.5 text-[11px] text-muted-foreground">先选定标的，助手会基于实时数据开始研究</p>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-1 rounded-xl bg-accent/40 p-1" role="group" aria-label="选择股票市场">
        {MARKETS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setMarket(item.value)}
            disabled={disabled}
            aria-pressed={market === item.value}
            className={`rounded-lg px-2 py-2 text-[12px] font-medium transition-colors ${
              market === item.value
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <label className="mt-3 flex items-center gap-2 rounded-xl border border-border/70 bg-card/50 px-3 focus-within:ring-2 focus-within:ring-primary/20">
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={disabled}
          className="h-11 min-w-0 flex-1 bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground/80"
          placeholder="输入股票代码或名称"
          aria-label="搜索股票"
          autoFocus
        />
        {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />}
      </label>

      <div className="mt-2 max-h-56 overflow-y-auto overscroll-contain">
        {error && <p className="px-3 py-3 text-[12px] text-destructive">{error}</p>}
        {!loading && !error && query.trim() && results.length === 0 && (
          <p className="px-3 py-3 text-[12px] text-muted-foreground">没有找到匹配的股票</p>
        )}
        <div className="space-y-1">
          {results.map((stock) => (
            <button
              key={`${stock.market}:${stock.symbol}`}
              type="button"
              onClick={() => onSelect(stock)}
              disabled={disabled}
              className="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 disabled:opacity-50"
            >
              <span className="min-w-0 truncate text-[13px] font-medium text-foreground">{stock.name}</span>
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{stock.market}:{stock.symbol}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
