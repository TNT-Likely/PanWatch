import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider, useToast } from '@panwatch/base-ui/components/ui/toast'

function Trigger({ action }: { action: () => void }) {
  const { toast } = useToast()
  return <button onClick={() => toast('研究已完成', 'success', { label: '查看会话', onClick: action })}>通知</button>
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('assistant notification toast actions', () => {
  it('runs its action once and dismisses the toast', () => {
    const action = vi.fn()
    render(<ToastProvider><Trigger action={action} /></ToastProvider>)
    fireEvent.click(screen.getByText('通知'))
    expect(screen.getByRole('status').textContent).toBe('研究已完成')
    fireEvent.click(screen.getByText('查看会话'))
    expect(action).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('keeps actionable notifications for eight seconds and clears timers on unmount', () => {
    const { unmount } = render(<ToastProvider><Trigger action={() => {}} /></ToastProvider>)
    fireEvent.click(screen.getByText('通知'))
    act(() => vi.advanceTimersByTime(7_999))
    expect(screen.getByText('查看会话')).toBeTruthy()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.queryByText('查看会话')).toBeNull()
    fireEvent.click(screen.getByText('通知'))
    act(() => vi.advanceTimersByTime(20))
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
