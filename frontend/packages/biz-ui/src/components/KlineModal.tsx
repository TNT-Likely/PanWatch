import { useCallback, useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import InteractiveKline, { type KlineInterval, klineDialogFullscreenClassName } from '@panwatch/biz-ui/components/InteractiveKline'
import { cn } from '@panwatch/base-ui'

export default function KlineModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  title?: string
  description?: string
  initialInterval?: KlineInterval
  initialDays?: '60' | '120' | '250'
}) {
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || '').trim() || 'CN'
  const [klineFullscreen, setKlineFullscreen] = useState(false)

  useEffect(() => {
    if (!props.open) setKlineFullscreen(false)
  }, [props.open])

  return (
    <Dialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open) setKlineFullscreen(false)
        props.onOpenChange(open)
      }}
    >
      <DialogContent
        className={cn(
          klineFullscreen
            ? klineDialogFullscreenClassName
            : 'max-w-6xl max-h-[calc(100vh-2rem)] overflow-y-auto',
        )}
      >
        {!klineFullscreen ? (
          <DialogHeader>
            <DialogTitle>{props.title || (symbol ? `K线：${symbol}` : 'K线')}</DialogTitle>
            <DialogDescription>
              {props.description || '5分/30分/日K/周K/月K切换，含MA/成交量/MACD。'}
            </DialogDescription>
          </DialogHeader>
        ) : null}
        {symbol ? (
          <InteractiveKline
            symbol={symbol}
            market={market}
            initialInterval={props.initialInterval}
            initialDays={props.initialDays}
            onFullscreenChange={setKlineFullscreen}
          />
        ) : (
          <div className="text-[12px] text-muted-foreground py-8 text-center">未选择股票</div>
        )}
      </DialogContent>
    </Dialog>
  )
}
