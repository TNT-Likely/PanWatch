import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, X } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import type { Account, PositionForm, SearchResult } from './types'

interface Props {
  positionDialogOpen: boolean
  setPositionDialogOpen: (open: boolean) => void
  editPositionId: number | null
  positionForm: PositionForm
  setPositionForm: (form: PositionForm) => void
  accounts: Account[]
  positionDialogAccountId: number | null
  positionSearchQuery: string
  setPositionSearchQuery: (value: string) => void
  positionSearchResults: SearchResult[]
  setPositionSearchResults: (value: SearchResult[]) => void
  showPositionDropdown: boolean
  setShowPositionDropdown: (value: boolean) => void
  positionSearchMarket: string
  setPositionSearchMarket: (value: string) => void
  positionSearching: boolean
  positionDropdownRef: RefObject<HTMLDivElement>
  handlePositionSearchMarketChange: (market: string) => void
  handlePositionSearchInput: (query: string) => void
  selectPositionStock: (item: SearchResult) => void
  handlePositionSubmit: () => void
  marketBadge: (market: string) => { label: string; style: string }
}
export function PositionDialog({ positionDialogOpen, setPositionDialogOpen, editPositionId, positionForm, setPositionForm, accounts, positionDialogAccountId, positionSearchQuery, setPositionSearchQuery, positionSearchResults, setPositionSearchResults, showPositionDropdown, setShowPositionDropdown, positionSearchMarket, setPositionSearchMarket, positionSearching, positionDropdownRef, handlePositionSearchMarketChange, handlePositionSearchInput, selectPositionStock, handlePositionSubmit, marketBadge }: Props) {
  const { t: stockT } = useTranslation('configuration')
  return (
    <Dialog
      open={positionDialogOpen}
      onOpenChange={(open) => {
        setPositionDialogOpen(open)
        if (!open) {
          setPositionSearchQuery('')
          setPositionSearchResults([])
          setShowPositionDropdown(false)
          setPositionSearchMarket('')
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editPositionId ? stockT('stocksPage.messages.editPosition') : stockT('stocksPage.messages.addPosition')}</DialogTitle>
          <DialogDescription>
            {accounts.find(a => a.id === positionDialogAccountId)?.name} {stockT('stocksPage.messages.positionInfo')}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 mt-2">
          {editPositionId ? (
            <div className="flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg bg-accent/30">
              <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                {marketBadge(positionForm.stock_market).label}
              </span>
              <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
              <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
              <Select value={positionForm.stock_asset_type} onValueChange={value => setPositionForm({ ...positionForm, stock_asset_type: value as 'stock' | 'etf' })}>
                <SelectTrigger aria-label={stockT('stocksPage.assetTypes.label')} className="w-28 h-8 shrink-0"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="stock">{stockT('stocksPage.assetTypes.stock')}</SelectItem>
                  <SelectItem value="etf">{stockT('stocksPage.assetTypes.etf')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Label className="mb-0">{stockT('stocksPage.messages.searchStock')}</Label>
                <div className="flex items-center gap-1">
                  {[
                    { value: '', label: stockT('stocksPage.markets.all') },
                    { value: 'CN', label: stockT('stocksPage.markets.cn') },
                    { value: 'HK', label: stockT('stocksPage.markets.hk') },
                    { value: 'US', label: stockT('stocksPage.markets.us') },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handlePositionSearchMarketChange(opt.value)}
                      className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                        positionSearchMarket === opt.value
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="relative" ref={positionDropdownRef}>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
                  <Input
                    value={positionSearchQuery}
                    onChange={e => handlePositionSearchInput(e.target.value)}
                    onFocus={() => positionSearchResults.length > 0 && setShowPositionDropdown(true)}
                    placeholder={stockT('stocksPage.messages.searchPlaceholder', { example: positionSearchMarket === 'HK' ? '00700 / 02800' : positionSearchMarket === 'US' ? 'AAPL / SPY' : positionSearchMarket === 'CN' ? '600519 / 510300' : '510300 / 02800 / SPY' })}
                    className="pl-9"
                    autoComplete="off"
                  />
                  {positionSearching && <span className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />}
                </div>
                {showPositionDropdown && positionSearchResults.length > 0 && (
                  <div className="relative w-full mt-1 max-h-48 overflow-auto overscroll-contain scrollbar card shadow-lg">
                    {positionSearchResults.map(item => (
                      <button
                        key={`${item.market}-${item.symbol}`}
                        type="button"
                        onClick={() => selectPositionStock(item)}
                        className="w-full flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-accent/50 text-left transition-colors"
                      >
                        <span className={`text-[9px] px-1 py-0.5 rounded ${marketBadge(item.market).style}`}>
                          {marketBadge(item.market).label}
                        </span>
                        <span className="font-mono text-muted-foreground text-[12px]">{item.symbol}</span>
                        <span className="flex-1 text-foreground">{item.name}</span>
                        {item.asset_type === 'etf' && <Badge variant="secondary">{stockT('stocksPage.assetTypes.etf')}</Badge>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {positionForm.stock_symbol && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                    {marketBadge(positionForm.stock_market).label}
                  </span>
                  <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
                  <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
                  <Select value={positionForm.stock_asset_type} onValueChange={value => setPositionForm({ ...positionForm, stock_asset_type: value as 'stock' | 'etf' })}>
                    <SelectTrigger aria-label={stockT('stocksPage.assetTypes.label')} className="w-28 h-8 shrink-0"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="stock">{stockT('stocksPage.assetTypes.stock')}</SelectItem>
                      <SelectItem value="etf">{stockT('stocksPage.assetTypes.etf')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <button
                    type="button"
                    onClick={() => {
                      setPositionForm({ ...positionForm, stock_id: 0, stock_symbol: '', stock_name: '', stock_market: '' })
                      setPositionSearchQuery('')
                    }}
                    className="ml-1 text-muted-foreground hover:text-destructive"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>{stockT('stocksPage.messages.costPrice')}</Label>
              <Input
                value={positionForm.cost_price}
                onChange={e => setPositionForm({ ...positionForm, cost_price: e.target.value })}
                placeholder="0.00"
                className="font-mono"
                inputMode="decimal"
              />
            </div>
            <div>
              <Label>{stockT('stocksPage.messages.quantity')}</Label>
              <Input
                value={positionForm.quantity}
                onChange={e => setPositionForm({ ...positionForm, quantity: e.target.value })}
                placeholder="0"
                className="font-mono"
                inputMode="numeric"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>{stockT('stocksPage.messages.investedAmount')} <span className="text-muted-foreground/60 text-[11px]">({stockT('stocksPage.messages.optional')})</span></Label>
              <Input
                value={positionForm.invested_amount}
                onChange={e => setPositionForm({ ...positionForm, invested_amount: e.target.value })}
                placeholder={stockT('stocksPage.messages.optional')}
                className="font-mono"
                inputMode="decimal"
              />
            </div>
            <div>
              <Label>{stockT('stocksPage.messages.tradingStyle')} <span className="text-muted-foreground font-normal">({stockT('stocksPage.messages.optional')})</span></Label>
              <Select
                value={positionForm.trading_style}
                onValueChange={val => setPositionForm({ ...positionForm, trading_style: val === '__none__' ? '' : val })}
              >
                <SelectTrigger>
                  <SelectValue placeholder={stockT('stocksPage.messages.unset')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{stockT('stocksPage.messages.unset')}</SelectItem>
                  <SelectItem value="short">{stockT('stocksPage.messages.shortStyle')}</SelectItem>
                  <SelectItem value="swing">{stockT('stocksPage.messages.swingStyle')}</SelectItem>
                  <SelectItem value="long">{stockT('stocksPage.messages.longStyle')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setPositionDialogOpen(false)}>{stockT('stocksPage.messages.cancel')}</Button>
            <Button
              onClick={handlePositionSubmit}
              disabled={!positionForm.cost_price || !positionForm.quantity || (!editPositionId && !positionForm.stock_id && !positionForm.stock_symbol)}
            >
              {editPositionId ? stockT('stocksPage.messages.save') : stockT('stocksPage.messages.add')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
