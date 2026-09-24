import { useEffect, useRef, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { fetchAPI, stocksApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { type SearchResult, type StockForm, emptyStockForm } from './types'

function marketLabel(m: string): string {
  return m === 'CN' ? 'A股' : m === 'HK' ? '港股' : m === 'US' ? '美股' : m
}

interface AddStockDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 添加成功后回调（父级刷新列表）。 */
  onAdded: () => void
}

/**
 * 添加股票到自选：搜索（500ms 防抖）+ 市场筛选 + 股票列表缓存刷新。
 * 搜索相关 state 与逻辑完全内聚，父级只传 open / onOpenChange / onAdded。
 */
export default function AddStockDialog({ open, onOpenChange, onAdded }: AddStockDialogProps) {
  const { toast } = useToast()
  const [stockForm, setStockForm] = useState<StockForm>(emptyStockForm)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMarket, setSearchMarket] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [searching, setSearching] = useState(false)
  const [refreshingStockList, setRefreshingStockList] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const dropdownRef = useRef<HTMLDivElement>(null)

  // 每次打开重置搜索与选中（对应旧实现「打开前手动清空」的语义）
  useEffect(() => {
    if (open) {
      setStockForm(emptyStockForm)
      setSearchQuery('')
      setSearchMarket('')
      setSearchResults([])
      setShowDropdown(false)
    }
  }, [open])

  // 点击外部关闭搜索下拉
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const doSearch = async (q: string, market: string = searchMarket) => {
    if (q.length < 1) {
      setSearchResults([])
      setShowDropdown(false)
      return
    }
    setSearching(true)
    try {
      const marketParam = market ? `&market=${market}` : ''
      const results = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q)}${marketParam}`)
      setSearchResults(results)
      setShowDropdown(results.length > 0)
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const handleSearchInput = (value: string) => {
    setSearchQuery(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => doSearch(value), 500)
  }

  const handleSearchMarketChange = (market: string) => {
    setSearchMarket(market)
    if (searchQuery) {
      doSearch(searchQuery, market)
    }
  }

  const refreshStockListCache = async () => {
    setRefreshingStockList(true)
    try {
      const result = await fetchAPI<{ count: number }>('/stocks/refresh-list', { method: 'POST' })
      toast(`已刷新股票列表，共 ${result.count} 只`, 'success')
      if (searchQuery) {
        doSearch(searchQuery)
      }
    } catch {
      toast('刷新失败', 'error')
    } finally {
      setRefreshingStockList(false)
    }
  }

  const selectStock = (item: SearchResult) => {
    setStockForm({ symbol: item.symbol, name: item.name, market: item.market })
    setSearchQuery(`${item.symbol} ${item.name}`)
    setShowDropdown(false)
  }

  const handleStockSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await stocksApi.create(stockForm)
      setStockForm(emptyStockForm)
      setSearchQuery('')
      onOpenChange(false)
      onAdded()
      toast('股票已添加', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '添加股票失败', 'error')
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) {
          setSearchQuery('')
          setSearchMarket('')
        }
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>添加股票到自选</DialogTitle>
          <DialogDescription>搜索并添加到自选股列表</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleStockSubmit}>
          <div className="relative" ref={dropdownRef}>
            <div className="flex items-center gap-2 mb-2">
              <Label className="mb-0">搜索股票</Label>
              <div className="flex items-center gap-1">
                {[
                  { value: '', label: '全部' },
                  { value: 'CN', label: 'A股' },
                  { value: 'HK', label: '港股' },
                  { value: 'US', label: '美股' },
                ].map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => handleSearchMarketChange(opt.value)}
                    className={`text-caption px-2 py-0.5 rounded transition-colors ${
                      searchMarket === opt.value
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={refreshStockListCache}
                disabled={refreshingStockList}
                className="text-mini text-muted-foreground hover:text-foreground transition-colors ml-2"
                title="搜索不到？点击刷新股票列表"
              >
                {refreshingStockList ? (
                  <span className="flex items-center gap-1">
                    <RefreshCw className="w-3 h-3 animate-spin" /> 刷新中...
                  </span>
                ) : (
                  <span className="flex items-center gap-1">
                    <RefreshCw className="w-3 h-3" /> 刷新列表
                  </span>
                )}
              </button>
            </div>
            <div className="relative">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                value={searchQuery}
                onChange={e => handleSearchInput(e.target.value)}
                onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
                placeholder={
                  searchMarket === 'HK'
                    ? '代码或名称，如 00700 或 腾讯'
                    : searchMarket === 'US'
                      ? '代码或名称，如 AAPL 或 苹果'
                      : '代码或名称，如 600519 或 茅台'
                }
                className="pl-10"
                autoComplete="off"
              />
              {searching && (
                <span className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
              )}
            </div>
            {showDropdown && (
              <div className="absolute z-50 w-full mt-2 max-h-64 overflow-auto scrollbar card shadow-lg">
                {searchResults.map(item => (
                  <button
                    key={`${item.market}-${item.symbol}`}
                    type="button"
                    onClick={() => selectStock(item)}
                    className="w-full flex items-center gap-3 px-4 py-3 text-body hover:bg-accent/50 text-left transition-colors"
                  >
                    <span className="font-mono text-muted-foreground text-body-sm w-14">{item.symbol}</span>
                    <span className="flex-1 font-medium text-foreground">{item.name}</span>
                    <Badge variant="secondary">{marketLabel(item.market)}</Badge>
                  </button>
                ))}
              </div>
            )}
            {stockForm.symbol && (
              <div className="mt-2.5 flex items-center gap-2">
                <Badge>
                  <span className="font-mono">{stockForm.symbol}</span> {stockForm.name}
                </Badge>
                <Badge variant="secondary">{marketLabel(stockForm.market)}</Badge>
              </div>
            )}
          </div>
          <div className="mt-6 flex items-center gap-3 justify-end">
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onOpenChange(false)
                setSearchQuery('')
              }}
            >
              取消
            </Button>
            <Button type="submit" disabled={!stockForm.symbol}>
              确认添加
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
