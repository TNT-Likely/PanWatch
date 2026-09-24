import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAPI } from '@panwatch/api'
import { AssistantStockPicker } from '@/components/assistant/AssistantStockPicker'

vi.mock('@panwatch/api', () => ({
  fetchAPI: vi.fn(),
}))

describe('AssistantStockPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('searches the selected market and returns the chosen stock', async () => {
    vi.mocked(fetchAPI).mockResolvedValue([
      { symbol: '600519', name: '贵州茅台', market: 'CN' },
    ])
    const onSelect = vi.fn()
    const user = userEvent.setup()

    render(<AssistantStockPicker onSelect={onSelect} onCancel={vi.fn()} />)

    await user.type(screen.getByRole('searchbox', { name: '搜索股票' }), '茅台')
    await waitFor(() => expect(fetchAPI).toHaveBeenCalledWith(
      '/stocks/search?q=%E8%8C%85%E5%8F%B0&market=CN',
      expect.any(Object),
    ))
    await user.click(await screen.findByRole('button', { name: /贵州茅台/ }))

    expect(onSelect).toHaveBeenCalledWith({ symbol: '600519', name: '贵州茅台', market: 'CN' })
  })
})
