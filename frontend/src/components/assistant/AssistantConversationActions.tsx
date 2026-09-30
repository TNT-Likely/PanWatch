import { useEffect, useRef, useState } from 'react'
import { Download, History, MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ChatConversation } from '@panwatch/api'
import { Popover, PopoverContent, PopoverTrigger } from '@panwatch/base-ui/components/ui/popover'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { AssistantContextExportDialog } from './AssistantContextExportDialog'
import { AssistantExportHistoryDialog } from './AssistantExportHistoryDialog'

interface Props {
  conversation: ChatConversation
  onRename?: (conversationId: number, title: string) => Promise<void>
  onDelete: (conversationId: number) => void
}
export function AssistantConversationActions({ conversation, onRename, onDelete }: Props) {
  const { t } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const [menu, setMenu] = useState(false)
  const [editing, setEditing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(false)
  const handingOff = useRef(false)
  const handoffTimer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => clearTimeout(handoffTimer.current), [])
  const label = conversation.title || tr('assistantPage.newResearch')
  return <>
    <Popover open={menu} onOpenChange={next => { if (next) handingOff.current = false; setMenu(next) }}>
      <PopoverTrigger asChild><button type="button" aria-label={tr('assistantPage.conversationActions', { title: label })} className="mr-1 shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground md:opacity-0 md:group-hover:opacity-100 md:focus:opacity-100 aria-expanded:opacity-100"><MoreHorizontal className="h-4 w-4" /></button></PopoverTrigger>
      <PopoverContent className="w-44 p-1" align="end" onCloseAutoFocus={event => { if (handingOff.current) event.preventDefault() }}>
        {onRename && <button type="button" onClick={() => { handingOff.current = true; setMenu(false); setTitle(conversation.title); setError(false); handoffTimer.current = setTimeout(() => setEditing(true), 0) }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] hover:bg-accent"><Pencil className="h-3.5 w-3.5" />{tr('assistantPage.rename')}</button>}
        <button type="button" onClick={() => { handingOff.current = true; setMenu(false); handoffTimer.current = setTimeout(() => setExporting(true), 0) }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] hover:bg-accent"><Download className="h-3.5 w-3.5" />{tr('assistantPage.exportContext.title')}</button>
        <button type="button" onClick={() => { handingOff.current = true; setMenu(false); handoffTimer.current = setTimeout(() => setHistoryOpen(true), 0) }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] hover:bg-accent"><History className="h-3.5 w-3.5" />{tr('assistantPage.exportHistory.title')}</button>
        <button type="button" onClick={() => { setMenu(false); onDelete(conversation.id) }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] text-destructive hover:bg-accent"><Trash2 className="h-3.5 w-3.5" />{tr('assistantPage.deleteConversation')}</button>
      </PopoverContent>
    </Popover>
    <Dialog open={editing} onOpenChange={next => { if (!saving) setEditing(next) }}>
      <DialogContent><DialogHeader><DialogTitle>{tr('assistantPage.rename')}</DialogTitle><DialogDescription>{tr('assistantPage.renameDescription')}</DialogDescription></DialogHeader>
        <form onSubmit={async event => {
          event.preventDefault()
          const value = title.trim()
          if (!onRename || !value || saving) return
          setSaving(true); setError(false)
          try { await onRename(conversation.id, value); setEditing(false) } catch { setError(true) } finally { setSaving(false) }
        }}>
          <label className="mb-2 block text-[12px]" htmlFor={`conversation-title-${conversation.id}`}>{tr('assistantPage.conversationTitle')}</label>
          <input id={`conversation-title-${conversation.id}`} value={title} onChange={event => setTitle(event.target.value)} maxLength={80} disabled={saving} autoFocus className="h-10 w-full rounded-lg border border-border bg-background px-3 text-[13px] outline-none focus:border-primary" />
          {error && <p role="alert" className="mt-2 text-[12px] text-destructive">{tr('assistantPage.renameFailed')}</p>}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" disabled={saving} onClick={() => setEditing(false)} className="rounded-lg border border-border px-3 py-2 text-[12px]">{tr('assistantPage.renameCancel')}</button>
            <button type="submit" disabled={saving || !title.trim()} className="rounded-lg bg-primary px-3 py-2 text-[12px] text-primary-foreground disabled:opacity-50">{tr(saving ? 'assistantPage.renameSaving' : 'assistantPage.renameSave')}</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
    {exporting && <AssistantContextExportDialog conversationId={conversation.id} onClose={() => setExporting(false)} />}
    {historyOpen && <AssistantExportHistoryDialog conversationId={conversation.id} onClose={() => setHistoryOpen(false)} />}
  </>
}
