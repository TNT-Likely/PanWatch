import { useRef, useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'

function ControlledDialog({ handoff = false }: { handoff?: boolean }) {
  const [open, setOpen] = useState(false)
  const destination = useRef<HTMLButtonElement>(null)
  return <>
    <button onClick={() => setOpen(true)}>Open dialog</button>
    <button ref={destination}>Destination</button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent onCloseAutoFocus={handoff ? event => { event.preventDefault(); destination.current?.focus() } : undefined}>
        <DialogTitle>Report</DialogTitle><DialogDescription>Report details</DialogDescription>
        <button>Inside</button>
      </DialogContent>
    </Dialog>
  </>
}

describe('controlled dialog focus', () => {
  it('restores the opener after Escape even without a DialogTrigger', async () => {
    render(<ControlledDialog />)
    const opener = screen.getByRole('button', { name: 'Open dialog' })
    opener.focus(); fireEvent.click(opener)
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(opener))
  })
  it('respects an explicit focus handoff', async () => {
    render(<ControlledDialog handoff />)
    const opener = screen.getByRole('button', { name: 'Open dialog' })
    opener.focus(); fireEvent.click(opener)
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Destination' })))
  })
})
