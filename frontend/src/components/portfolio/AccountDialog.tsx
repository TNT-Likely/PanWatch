import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import type { AccountForm } from './types'

interface Props { open: boolean; onOpenChange: (open: boolean) => void; editing: boolean; value: AccountForm; onChange: (value: AccountForm) => void; onSubmit: () => void }
export function AccountDialog({ open, onOpenChange, editing, value, onChange, onSubmit }: Props) {
  const { t: stockT } = useTranslation('configuration')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? stockT('stocksPage.messages.editAccount') : stockT('stocksPage.messages.addAccountTitle')}</DialogTitle>
          <DialogDescription>{stockT('stocksPage.messages.accountInfo')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 mt-2">
          <div>
            <Label>{stockT('stocksPage.messages.accountName')}</Label>
            <Input
              value={value.name}
              onChange={e => onChange({ ...value, name: e.target.value })}
              placeholder={stockT('stocksPage.messages.accountName')}
            />
          </div>
          <div>
            <Label>{stockT('stocksPage.messages.availableFunds')}</Label>
            <Input
              value={value.available_funds}
              onChange={e => onChange({ ...value, available_funds: e.target.value })}
              placeholder="0"
              className="font-mono"
              inputMode="decimal"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>{stockT('stocksPage.messages.cancel')}</Button>
            <Button onClick={onSubmit} disabled={!value.name}>
              {editing ? stockT('stocksPage.messages.save') : stockT('stocksPage.messages.create')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
