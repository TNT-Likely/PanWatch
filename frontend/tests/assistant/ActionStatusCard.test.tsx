import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActionStatusCard } from '@/components/assistant/ActionStatusCard'

describe('ActionStatusCard', () => {
  it('explains that no write ran and offers a retry action', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()

    render(
      <ActionStatusCard
        status="needs_retry"
        message="我还没有执行这次修改，请确认目标后重试。"
        onRetry={onRetry}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('尚未执行')
    expect(screen.getByText('我还没有执行这次修改，请确认目标后重试。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '重试执行' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
