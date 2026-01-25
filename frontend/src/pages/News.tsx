import { useState, useEffect, useCallback } from 'react'
import { Newspaper, RefreshCw, Clock, Star, Filter, ExternalLink } from 'lucide-react'
import { fetchAPI } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { useToast } from '@/components/ui/toast'

interface NewsItem {
  source: string
  source_label: string
  external_id: string
  title: string
  content: string
  publish_time: string
  symbols: string[]
  importance: number
  url: string
}

interface Stock {
  id: number
  symbol: string
  name: string
}

const TIME_OPTIONS = [
  { value: '2', label: '最近 2 小时' },
  { value: '6', label: '最近 6 小时' },
  { value: '12', label: '最近 12 小时' },
  { value: '24', label: '最近 24 小时' },
  { value: '48', label: '最近 48 小时' },
]

const SOURCE_COLORS: Record<string, string> = {
  xueqiu: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  eastmoney_news: 'bg-cyan-500/10 text-cyan-600 border-cyan-500/20',
  eastmoney: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
}

export default function NewsPage() {
  const { toast } = useToast()
  const [news, setNews] = useState<NewsItem[]>([])
  const [stocks, setStocks] = useState<Stock[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedSymbol, setSelectedSymbol] = useState<string>('all')
  const [timeRange, setTimeRange] = useState<string>('12')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [filterRelated] = useState(true)

  // 加载自选股列表
  useEffect(() => {
    fetchAPI<Stock[]>('/stocks')
      .then(data => setStocks(data || []))
      .catch(() => {})
  }, [])

  // 加载新闻
  const loadNews = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedSymbol && selectedSymbol !== 'all') {
        params.set('symbols', selectedSymbol)
      }
      params.set('hours', timeRange)
      params.set('limit', '100')
      params.set('filter_related', filterRelated.toString())

      const data = await fetchAPI<NewsItem[]>(`/news?${params.toString()}`)
      setNews(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [selectedSymbol, timeRange, filterRelated, toast])

  useEffect(() => {
    loadNews()
  }, [loadNews])

  // 自动刷新
  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(loadNews, 60000) // 每分钟刷新
    return () => clearInterval(interval)
  }, [autoRefresh, loadNews])

  // 获取股票名称
  const getStockName = (symbol: string) => {
    const stock = stocks.find(s => s.symbol === symbol)
    return stock?.name || symbol
  }

  // 重要性星星
  const renderImportance = (level: number) => {
    if (level === 0) return null
    return (
      <span className="inline-flex items-center gap-0.5 text-amber-500">
        {Array.from({ length: Math.min(level, 3) }).map((_, i) => (
          <Star key={i} className="w-3 h-3 fill-current" />
        ))}
      </span>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2 md:gap-3">
          <div className="w-9 h-9 md:w-10 md:h-10 rounded-lg md:rounded-xl bg-blue-500 flex items-center justify-center">
            <Newspaper className="w-4 h-4 md:w-5 md:h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg md:text-xl font-bold">新闻中心</h1>
            <p className="text-[12px] md:text-[13px] text-muted-foreground">实时财经快讯与个股公告</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant={autoRefresh ? 'default' : 'outline'}
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
            className="gap-1.5 h-8"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${autoRefresh ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">{autoRefresh ? '自动刷新中' : '自动刷新'}</span>
          </Button>
          <Button variant="outline" size="sm" className="h-8 w-8 p-0" onClick={loadNews} disabled={loading}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 md:gap-3">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-muted-foreground hidden sm:block" />
          <Select value={selectedSymbol} onValueChange={setSelectedSymbol}>
            <SelectTrigger className="w-[130px] md:w-[160px] h-8 md:h-9 text-[12px] md:text-sm">
              <SelectValue placeholder="全部股票" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部股票</SelectItem>
              {stocks.map(stock => (
                <SelectItem key={stock.symbol} value={stock.symbol}>
                  {stock.name} ({stock.symbol})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-muted-foreground hidden sm:block" />
          <Select value={timeRange} onValueChange={setTimeRange}>
            <SelectTrigger className="w-[100px] md:w-[140px] h-8 md:h-9 text-[12px] md:text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIME_OPTIONS.map(opt => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <span className="text-[11px] md:text-[12px] text-muted-foreground ml-auto">
          共 {news.length} 条
        </span>
      </div>

      {/* News List */}
      {loading && news.length === 0 ? (
        <div className="card p-12 text-center">
          <div className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto" />
        </div>
      ) : news.length === 0 ? (
        <div className="card p-12 text-center">
          <Newspaper className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
          <p className="text-muted-foreground">暂无相关新闻</p>
        </div>
      ) : (
        <div className="card divide-y divide-border/50">
          {news.map((item, idx) => (
            <div key={`${item.source}-${item.external_id}-${idx}`} className="p-4 hover:bg-accent/30 transition-colors">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  {/* Title Row */}
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge
                      variant="outline"
                      className={`text-[10px] flex-shrink-0 ${SOURCE_COLORS[item.source] || ''}`}
                    >
                      {item.source_label}
                    </Badge>
                    {renderImportance(item.importance)}
                    {item.url ? (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[14px] font-medium leading-snug line-clamp-2 hover:text-primary hover:underline flex items-center gap-1 group"
                        onClick={e => e.stopPropagation()}
                      >
                        {item.title}
                        <ExternalLink className="w-3 h-3 opacity-0 group-hover:opacity-100 flex-shrink-0 transition-opacity" />
                      </a>
                    ) : (
                      <h3 className="text-[14px] font-medium leading-snug line-clamp-2">
                        {item.title}
                      </h3>
                    )}
                  </div>

                  {/* Content Preview */}
                  {item.content && (
                    <p className="text-[13px] text-muted-foreground line-clamp-2 mb-2">
                      {item.content}
                    </p>
                  )}

                  {/* Meta Row */}
                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                    <span>{item.publish_time}</span>
                    {item.symbols.length > 0 && (
                      <div className="flex items-center gap-1">
                        {item.symbols.slice(0, 3).map(symbol => (
                          <Badge
                            key={symbol}
                            variant="secondary"
                            className="text-[10px] px-1.5 py-0"
                          >
                            {getStockName(symbol)}
                          </Badge>
                        ))}
                        {item.symbols.length > 3 && (
                          <span className="text-muted-foreground">+{item.symbols.length - 3}</span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
