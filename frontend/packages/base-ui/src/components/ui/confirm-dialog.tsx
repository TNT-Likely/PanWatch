import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from './button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './dialog'

interface ConfirmOptions { title?: string; confirmLabel?: string; destructive?: boolean }
type ConfirmAction = (description: string, options?: ConfirmOptions) => Promise<boolean>
interface Request { description: string; options: ConfirmOptions; resolve: (accepted: boolean) => void; opener: HTMLElement | null }
const ConfirmContext = createContext<ConfirmAction | null>(null)

export function ConfirmProvider({ children, labels }: {
  children: ReactNode; labels: { title: string; cancel: string; confirm: string }
}) {
  const [request, setRequest] = useState<Request | null>(null)
  const active = useRef<Request | null>(null)
  const queued = useRef<Request[]>([])
  const cancelButton = useRef<HTMLButtonElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)
  const confirmAction = useCallback<ConfirmAction>((description, options = {}) => new Promise(resolve => {
    const next = { description, options, resolve, opener: document.activeElement as HTMLElement | null }
    if (active.current) queued.current.push(next)
    else { active.current = next; setRequest(next) }
  }), [])
  const settle = useCallback((accepted: boolean) => {
    const current = active.current
    if (!current) return
    restoreFocus.current = current.opener
    current.resolve(accepted)
    const next = queued.current.shift() ?? null
    active.current = next
    setRequest(next)
  }, [])
  useEffect(() => () => {
    active.current?.resolve(false)
    queued.current.forEach(item => item.resolve(false))
    active.current = null
    queued.current = []
  }, [])

  return <ConfirmContext.Provider value={confirmAction}>
    {children}
    <Dialog open={request !== null} onOpenChange={value => { if (!value) settle(false) }}>
      <DialogContent className="max-w-sm" onOpenAutoFocus={event => { event.preventDefault(); cancelButton.current?.focus() }}
        onCloseAutoFocus={event => { event.preventDefault(); restoreFocus.current?.focus() }}>
        <DialogHeader>
          <DialogTitle>{request?.options.title ?? labels.title}</DialogTitle>
          <DialogDescription className="whitespace-pre-wrap break-words">{request?.description}</DialogDescription>
        </DialogHeader>
        <div className="flex justify-end gap-2">
          <Button ref={cancelButton} variant="outline" onClick={() => settle(false)}>{labels.cancel}</Button>
          <Button variant={request?.options.destructive ? 'destructive' : 'default'} onClick={() => settle(true)}>{request?.options.confirmLabel ?? labels.confirm}</Button>
        </div>
      </DialogContent>
    </Dialog>
  </ConfirmContext.Provider>
}

export function useConfirm(): ConfirmAction {
  const confirmAction = useContext(ConfirmContext)
  return useCallback((description, options) => {
    if (!confirmAction) throw new Error('useConfirm requires ConfirmProvider')
    return confirmAction(description, options)
  }, [confirmAction])
}
