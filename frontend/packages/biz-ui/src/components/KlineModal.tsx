import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'
import { useTranslation } from 'react-i18next'

export default function KlineModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  title?: string
  description?: string
  initialInterval?: '1d' | '1w' | '1m'
  initialDays?: '60' | '120' | '250'
}) {
  const { t } = useTranslation('bizUi')
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || '').trim() || 'CN'

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>{props.title || (symbol ? t('klineModal.title', { symbol }) : t('klineModal.fallbackTitle'))}</DialogTitle>
          <DialogDescription>
            {props.description || t('klineModal.description')}
          </DialogDescription>
        </DialogHeader>
        {symbol ? (
          <InteractiveKline
            symbol={symbol}
            market={market}
            initialInterval={props.initialInterval}
            initialDays={props.initialDays}
          />
        ) : (
          <div className="text-[12px] text-muted-foreground py-8 text-center">{t('klineModal.noStock')}</div>
        )}
      </DialogContent>
    </Dialog>
  )
}
