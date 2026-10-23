import { useTranslation } from 'react-i18next'
import { Newspaper, ExternalLink, RefreshCw } from 'lucide-react'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { formatDateTime } from '@/lib/utils'
import type { Stock, NewsItem } from './types'

interface Props { open: boolean; onOpenChange: (open: boolean) => void; symbol: string; onSymbolChange: (symbol: string) => void; loading: boolean; news: NewsItem[]; stocks: Stock[]; onLoad: (symbol?: string) => void }
export function NewsDialog({ open, onOpenChange, symbol, onSymbolChange, loading, news, stocks, onLoad }: Props) {
  const { t: stockT } = useTranslation('configuration')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Newspaper className="w-5 h-5 text-blue-500" />
            {stockT('stocksPage.messages.relatedNews')}
          </DialogTitle>
          <DialogDescription>
            {symbol
              ? stockT('stocksPage.messages.newsTitleForStock', { name: symbol })
              : stockT('stocksPage.messages.newsTitleAll')
            }
          </DialogDescription>
        </DialogHeader>

        {/* 股票筛选器 */}
        <div className="flex items-center gap-2 flex-wrap py-2 border-b">
          <span className="text-[12px] text-muted-foreground">{stockT('stocksPage.messages.newsFilter')}</span>
          <button
            onClick={() => { onSymbolChange(''); onLoad() }}
            className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
              !symbol
                ? 'bg-primary text-primary-foreground'
                : 'bg-accent/50 text-muted-foreground hover:bg-accent'
            }`}
          >
            {stockT('stocksPage.markets.all')}
          </button>
          {stocks.slice(0, 10).map(stock => (
            <button
              key={stock.symbol}
              onClick={() => { onSymbolChange(stock.name); onLoad(stock.name) }}
              className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                symbol === stock.name
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-accent/50 text-muted-foreground hover:bg-accent'
              }`}
            >
              {stock.name}
              {stock.asset_type === 'etf' && <Badge variant="secondary" className="ml-1">{stockT('stocksPage.assetTypes.etf')}</Badge>}
            </button>
          ))}
          {stocks.length > 10 && (
            <span className="text-[10px] text-muted-foreground">+{stocks.length - 10}</span>
          )}
        </div>

        {/* 新闻列表 */}
        <div className="flex-1 overflow-y-auto min-h-0 py-2 scrollbar">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
              <span className="ml-2 text-[13px] text-muted-foreground">{stockT('stocksPage.messages.loading')}</span>
            </div>
          ) : news.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground text-[13px]">
              {stockT('stocksPage.messages.noAnalysis')}
            </div>
          ) : (
            <div className="space-y-2">
              {news.map((item, idx) => (
                <div
                  key={`${item.source}-${item.external_id}-${idx}`}
                  className="p-3 rounded-lg bg-accent/30 hover:bg-accent/50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          item.source === 'eastmoney' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' :
                          item.source === 'eastmoney_news' ? 'bg-blue-500/10 text-blue-500' :
                          'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                        }`}>
                          {item.source_label}
                        </span>
                        {item.importance >= 2 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-500">
                            {stockT('stocksPage.messages.announcement')}
                          </span>
                        )}
                        <span className="text-[10px] text-muted-foreground">
                          {formatDateTime(item.publish_time)}
                        </span>
                      </div>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[13px] font-medium text-foreground hover:text-primary transition-colors block"
                      >
                        {item.title}
                      </a>
                      {item.symbols.length > 0 && (
                        <div className="flex items-center gap-1.5 mt-2">
                          {item.symbols.slice(0, 5).map(sym => {
                            const stockInfo = stocks.find(s => s.symbol === sym)
                            const stockName = stockInfo?.name || sym
                            return (
                              <button
                                key={sym}
                                onClick={() => { onSymbolChange(stockName); onLoad(stockName) }}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono hover:bg-primary/20 transition-colors"
                              >
                                {stockName}
                              </button>
                            )
                          })}
                          {item.symbols.length > 5 && (
                            <span className="text-[10px] text-muted-foreground">+{item.symbols.length - 5}</span>
                          )}
                        </div>
                      )}
                    </div>
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex-shrink-0 p-1.5 rounded-md hover:bg-accent transition-colors"
                      title={stockT('stocksPage.messages.viewOriginal')}
                    >
                      <ExternalLink className="w-4 h-4 text-muted-foreground" />
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 底部刷新按钮 */}
        <div className="flex items-center justify-between pt-2 border-t">
          <span className="text-[11px] text-muted-foreground">
            {news.length} {stockT('stocksPage.messages.amountUnit')} {stockT('stocksPage.messages.stockNews')}
          </span>
          <Button variant="secondary" size="sm" onClick={() => onLoad(symbol || undefined)} disabled={loading}>
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            {stockT('stocksPage.messages.refreshed')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
