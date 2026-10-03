import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider, useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'

const labels = { title: '确认', cancel: '取消', confirm: '确认' }
afterEach(cleanup)
function DeleteButton({ remove }: { remove: () => void }) {
  const confirmAction = useConfirm()
  return <button onClick={async () => {
    if (await confirmAction('确定删除账户？', { destructive: true })) remove()
  }}>删除账户</button>
}
describe('shared confirmation', () => {
  it('waits for acceptance, focuses cancel first, and restores focus on cancellation', async () => {
    const remove = vi.fn()
    render(<ConfirmProvider labels={labels}><DeleteButton remove={remove} /></ConfirmProvider>)
    const trigger = screen.getByRole('button', { name: '删除账户' })
    await userEvent.click(trigger)
    expect(await screen.findByRole('dialog')).toBeTruthy()
    expect(remove).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }))
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(remove).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(trigger)
    await userEvent.click(trigger)
    await userEvent.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1))
  })

  it('cancels pending confirmations on unmount', async () => {
    const remove = vi.fn()
    const view = render(<ConfirmProvider labels={labels}><DeleteButton remove={remove} /></ConfirmProvider>)
    await userEvent.click(screen.getByRole('button', { name: '删除账户' }))
    view.unmount()
    await Promise.resolve()
    expect(remove).not.toHaveBeenCalled()
  })

  it('keeps concurrent requests in order without losing a promise', async () => {
    const first = vi.fn(), second = vi.fn()
    function Trigger() {
      const ask = useConfirm()
      return <button onClick={() => {
        void ask('第一个请求').then(first)
        void ask('第二个请求').then(second)
      }}>打开</button>
    }
    render(<ConfirmProvider labels={labels}><Trigger /></ConfirmProvider>)
    await userEvent.click(screen.getByRole('button', { name: '打开' }))
    await userEvent.click(screen.getByRole('button', { name: '确认' }))
    expect(first).toHaveBeenCalledWith(true)
    expect(second).not.toHaveBeenCalled()
    expect(screen.getByText('第二个请求')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(second).toHaveBeenCalledWith(false)
  })
})
