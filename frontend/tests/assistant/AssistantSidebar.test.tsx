import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantSidebar } from '@/components/assistant/AssistantSidebar'

describe('AssistantSidebar', () => {
  const conversation = { id: 8, title: '贵州茅台走势', created_at: '2026-09-12T00:00:00Z' }

  it('animates running conversations and distinguishes approval waits and completed tasks', () => {
    const props = { conversations: [conversation], activeConversationId: null, onOpen: vi.fn(), onCreate: vi.fn(), onDelete: vi.fn() }
    const view = render(<AssistantSidebar {...props} taskStatuses={{ 8: 'running' }} />)
    expect(screen.getByRole('status', { name: '研究中' }).querySelector('svg')?.classList.contains('animate-spin')).toBe(true)
    view.rerender(<AssistantSidebar {...props} taskStatuses={{ 8: 'awaiting_approval' }} />)
    expect(screen.getByRole('status', { name: '等待审批' }).querySelector('svg')?.classList.contains('animate-spin')).toBe(false)
    view.rerender(<AssistantSidebar {...props} taskStatuses={{ 8: 'completed' }} />)
    expect(screen.queryByTestId('conversation-task-indicator')).toBeNull()
  })

  it('renames through the menu without opening the conversation and supports cancel', async () => {
    const user = userEvent.setup(); const onRename = vi.fn().mockResolvedValue(undefined); const onOpen = vi.fn()
    render(<AssistantSidebar conversations={[conversation]} activeConversationId={null} onOpen={onOpen} onCreate={vi.fn()} onDelete={vi.fn()} onRename={onRename} />)
    await user.click(screen.getByRole('button', { name: '会话操作：贵州茅台走势' }))
    await user.click(screen.getByRole('button', { name: '重命名' }))
    const input = screen.getByRole('textbox', { name: '会话标题' })
    await user.clear(input); await user.type(input, '我的长期研究')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(onRename).toHaveBeenCalledWith(8, '我的长期研究'))
    expect(onOpen).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '会话操作：贵州茅台走势' }))
    await user.click(screen.getByRole('button', { name: '重命名' }))
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onRename).toHaveBeenCalledTimes(1)
  })

  it('rejects a blank title and keeps a failed rename editable', async () => {
    const user = userEvent.setup(); const onRename = vi.fn().mockRejectedValue(new Error('offline'))
    render(<AssistantSidebar conversations={[conversation]} activeConversationId={null} onOpen={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onRename={onRename} />)
    await user.click(screen.getByRole('button', { name: '会话操作：贵州茅台走势' }))
    await user.click(screen.getByRole('button', { name: '重命名' }))
    const input = screen.getByRole('textbox', { name: '会话标题' })
    await user.clear(input); await user.type(input, '   ')
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true)
    await user.clear(input); await user.type(input, '可重试标题')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect((await screen.findByRole('alert')).textContent).toContain('重命名失败')
    expect((input as HTMLInputElement).value).toBe('可重试标题')
  })
  it('opens a prior research conversation and exposes a new-research action', async () => {
    const onOpen = vi.fn()
    const onCreate = vi.fn()
    const user = userEvent.setup()

    render(
      <AssistantSidebar
        conversations={[
          { id: 8, title: '贵州茅台走势', stock_symbol: '600519', stock_market: 'CN', created_at: '2026-09-12T00:00:00Z' },
        ]}
        activeConversationId={null}
        onOpen={onOpen}
        onCreate={onCreate}
        onDelete={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: '新研究' }))
    await user.click(screen.getByRole('button', { name: '贵州茅台走势' }))

    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 8 }))
  })
})
