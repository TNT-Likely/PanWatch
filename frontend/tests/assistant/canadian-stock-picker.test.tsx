import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { changeLocale } from '@/i18n'
import { AssistantStockPicker } from '@/components/assistant/AssistantStockPicker'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))

beforeEach(async () => {
  vi.clearAllMocks()
  await changeLocale('en-US')
})

describe('Canadian research stock picker', () => {
  it.each([
    ['TSX', 'REI-UN', 'RioCan REIT'],
    ['TSX', 'ZSP', 'BMO S&P 500 ETF'],
    ['TSXV', 'TBRD', 'Thunderbird'],
  ])('searches and selects %s:%s without changing the market', async (market, symbol, name) => {
    const stock = { market, symbol, name }
    vi.mocked(fetchAPI).mockResolvedValue([stock])
    const onSelect = vi.fn()
    render(<AssistantStockPicker onSelect={onSelect} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: market === 'TSXV' ? 'TSX Venture' : 'TSX', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: symbol } })
    await waitFor(() => expect(fetchAPI).toHaveBeenCalledWith(`/stocks/search?q=${symbol}&market=${market}`, expect.anything()))
    fireEvent.click(await screen.findByRole('button', { name: `${name} ${market}:${symbol}` }))
    expect(onSelect).toHaveBeenCalledWith(stock)
  })
})
