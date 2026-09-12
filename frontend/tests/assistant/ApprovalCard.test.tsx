import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApprovalCard } from '@/components/assistant/ApprovalCard'

describe('ApprovalCard', () => {
  it('allows exactly one explicit decision and shows the operation summary', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: '创建提醒',
          risk: 'write',
          summary: '为贵州茅台创建价格提醒',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'pending',
        }}
        onDecision={onDecision}
      />,
    )

    expect(screen.getByText('为贵州茅台创建价格提醒')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '本次允许' }))

    expect(onDecision).toHaveBeenCalledTimes(1)
    expect(onDecision).toHaveBeenCalledWith('approved')
    expect((screen.getByRole('button', { name: '拒绝' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps a decided card visible with its execution status', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: '创建提醒',
          risk: 'write',
          summary: '为贵州茅台创建价格提醒',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'approved',
        }}
        onDecision={vi.fn()}
      />,
    )

    expect(screen.getByText('已允许，已执行')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '本次允许' })).toBeNull()
  })

  it('shows a rejected card as a terminal decision without actions', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-2',
          tool_title: '创建提醒',
          risk: 'write',
          summary: '为贵州茅台创建价格提醒',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'rejected',
        }}
        onDecision={vi.fn()}
      />,
    )

    const status = screen.getByText('已拒绝，不会执行')
    expect(status.className).toContain('text-destructive')
    expect(screen.queryByRole('button', { name: '拒绝' })).toBeNull()
  })
})
