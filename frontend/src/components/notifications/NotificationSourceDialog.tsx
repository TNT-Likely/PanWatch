import { useTranslation } from 'react-i18next'
import type { NotificationTarget } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { AssistantMarkdown } from '@/components/assistant/AssistantMarkdown'
import { formatDate } from '@/i18n/format'
import { notificationTitle } from '@/lib/notifications'

export default function NotificationSourceDialog({ target, onClose }: { target: Extract<NotificationTarget, { kind: 'agent_run' | 'price_alert_hit' }>; onClose: () => void }) {
  const { t } = useTranslation('configuration')
  const translate = t as unknown as (key: string) => string
  const tr = (key: string) => translate(`notifications.${key}`)
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}><DialogContent className="p-4 sm:p-6" data-testid="notification-source-detail">
    <DialogHeader><DialogTitle>{tr(target.kind === 'agent_run' ? 'report' : 'hit')}</DialogTitle><DialogDescription>{formatDate(target.occurred_at)}</DialogDescription></DialogHeader>
    {target.kind === 'agent_run' ? <>
      <p className="text-[13px] font-semibold">{notificationTitle({ source: 'agent', title: target.agent_name, template_params: target.template_params || {} }, translate)}</p>
      <p className="text-[12px]">{tr(target.status === 'success' ? 'runCompleted' : 'runFailed')}</p>
      {target.notify_attempted && <p className="text-[12px] text-muted-foreground">{tr(target.notify_sent ? 'deliverySent' : 'deliveryFailed')}</p>}
      {target.result && <AssistantMarkdown content={target.result} />}
      {target.error && <p className="whitespace-pre-wrap break-words text-[12px] text-destructive">{target.error}</p>}
    </> : <>
      <p className="text-[13px] font-semibold">{target.name} ({target.symbol})</p>
      <p className="text-[12px]">{tr('price')}: {target.snapshot.quote?.current_price ?? '--'}</p>
      {target.deliveries?.length ? <div className="space-y-2 text-xs">
        {target.deliveries.map(delivery => <div key={delivery.id} className="rounded bg-accent p-2 break-words">
          <p>#{delivery.channel_id ?? '--'} · {translate(`priceAlerts.health.deliveryStates.${delivery.status}`)}</p>
          <p className="font-mono">{delivery.event_id}</p>
          {delivery.error_code && <p>{translate(`priceAlerts.health.errors.${delivery.error_code}`)}</p>}
        </div>)}
        <p className="text-muted-foreground">{translate('priceAlerts.health.boundary')}</p>
      </div> : <p className="text-[12px] text-muted-foreground">{target.notify_success ? tr('deliverySent') : translate('priceAlerts.health.legacyUnknown')}</p>}
      <ul className="space-y-2">{target.snapshot.conditions?.filter(item => item.matched).map((item, index) => <li className="rounded-lg bg-accent p-2 text-[12px]" key={index}>
        {tr(`metrics.${['price', 'change_pct', 'turnover', 'volume', 'volume_ratio'].includes(item.type) ? item.type : 'other'}`)} {item.op} {Array.isArray(item.target) ? item.target.join(' – ') : String(item.target)} · {tr('actual')}: {item.actual ?? '--'}
      </li>)}</ul>
    </>}
  </DialogContent></Dialog>
}
