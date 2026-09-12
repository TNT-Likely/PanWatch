import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chatApi } from '@panwatch/api'
import ChatWidget from '@/components/ChatWidget'

vi.mock('@panwatch/api', () => ({
  chatApi: {
    listConversations: vi.fn().mockResolvedValue([]),
    createConversation: vi.fn().mockResolvedValue({
      id: 1,
      title: '',
      stock_symbol: null,
      stock_market: null,
      created_at: '2026-09-12T00:00:00Z',
    }),
    sendAssistantMessageStream: vi.fn().mockResolvedValue(undefined),
  },
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ChatWidget layout', () => {
  it('keeps the composer at the bottom while only the message list scrolls', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    await waitFor(() => expect(screen.getByPlaceholderText('输入问题...')).toBeTruthy())

    const shell = screen.getByTestId('assistant-shell')
    const messageList = screen.getByTestId('assistant-message-list')
    const composer = screen.getByTestId('assistant-composer')

    expect(shell.className).toContain('min-h-0')
    expect(shell.className).toContain('h-[calc(100dvh-8rem)]')
    expect(messageList.className).toContain('min-h-0')
    expect(messageList.className).toContain('overflow-y-auto')
    expect(composer.className).toContain('shrink-0')
  })

  it('ignores a second send fired before the first request updates React state', async () => {
    render(<ChatWidget embedded />)
    const quickQuestion = await screen.findByRole('button', { name: '诊断我的持仓' })

    fireEvent.click(quickQuestion)
    fireEvent.click(quickQuestion)

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledTimes(1))
    expect(chatApi.sendAssistantMessageStream).toHaveBeenCalledTimes(1)
  })

  it('shows a prominent centered control when the reader scrolls away from the latest message', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))
    const messageList = await screen.findByTestId('assistant-message-list')

    Object.defineProperties(messageList, {
      scrollHeight: { configurable: true, value: 1000, writable: true },
      scrollTop: { configurable: true, value: 0, writable: true },
      clientHeight: { configurable: true, value: 500, writable: true },
    })
    fireEvent.scroll(messageList)

    const scrollButton = await screen.findByRole('button', { name: '回到底部' })
    expect(scrollButton.textContent).toContain('回到底部')
    expect(scrollButton.className).toContain('left-1/2')
    expect(scrollButton.className).toContain('h-10')

    await user.click(scrollButton)
    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })
})
