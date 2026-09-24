import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'

describe('AssistantWelcome', () => {
  it('starts a focused research question from a suggested entry point', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(<AssistantWelcome onSubmit={onSubmit} />)

    expect(screen.getByRole('heading', { name: '今天想研究什么？' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    expect(onSubmit).toHaveBeenCalledWith('诊断我的持仓风险和关键关注点')
  })

  it('opens stock selection before starting a single-stock analysis', async () => {
    const user = userEvent.setup()

    render(<AssistantWelcome onSubmit={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '分析一只股票' }))

    expect(screen.getByTestId('assistant-stock-picker')).toBeTruthy()
    expect(screen.getByRole('searchbox', { name: '搜索股票' })).toBeTruthy()
  })
})
