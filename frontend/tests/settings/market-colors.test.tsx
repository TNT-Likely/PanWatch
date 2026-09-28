import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { changeLocale } from '@/i18n'
import { MarketColorProvider, useMarketColors } from '@/hooks/use-market-colors'
import {
  MARKET_COLOR_STORAGE_KEY,
  getMarketColorPalette,
  marketColorWithAlpha,
  marketDirection,
  marketSignTextClass,
  normalizeMarketColorPreference,
  resolveMarketColorScheme,
} from '@/lib/market-colors'

function Probe() {
  const { preference, effectiveScheme, setPreference } = useMarketColors()
  return (
    <div>
      <span data-testid="preference">{preference}</span>
      <span data-testid="effective">{effectiveScheme}</span>
      <button onClick={() => setPreference('green-up')}>green-up</button>
      <button onClick={() => setPreference('auto')}>auto</button>
    </div>
  )
}

afterEach(async () => {
  window.localStorage.removeItem(MARKET_COLOR_STORAGE_KEY)
  await act(async () => {
    await changeLocale('zh-CN')
  })
  delete document.documentElement.dataset.marketColorScheme
})

describe('market color preferences', () => {
  it('resolves automatic defaults from the interface language', () => {
    expect(resolveMarketColorScheme('auto', 'zh-CN')).toBe('red-up')
    expect(resolveMarketColorScheme('auto', 'en-US')).toBe('green-up')
    expect(resolveMarketColorScheme('red-up', 'en-US')).toBe('red-up')
    expect(resolveMarketColorScheme('green-up', 'zh-CN')).toBe('green-up')
    expect(normalizeMarketColorPreference('legacy')).toBe('auto')
  })

  it('maps positive, negative, zero, and missing values to semantic classes', () => {
    expect(marketDirection(1)).toBe('up')
    expect(marketDirection(-1)).toBe('down')
    expect(marketDirection(0)).toBe('flat')
    expect(marketDirection(null)).toBe('flat')
    expect(marketSignTextClass(1)).toBe('text-market-up')
    expect(marketSignTextClass(-1)).toBe('text-market-down')
    expect(marketSignTextClass(0)).toBe('text-market-flat')
  })

  it('inverts concrete palettes without changing semantic directions', () => {
    const redUp = getMarketColorPalette('red-up')
    const greenUp = getMarketColorPalette('green-up')

    expect(redUp.up).toEqual(greenUp.down)
    expect(redUp.down).toEqual(greenUp.up)
    expect(marketColorWithAlpha(redUp.up.text, 0.25)).toBe('rgba(225, 29, 72, 0.25)')
    expect(marketColorWithAlpha(redUp.up.text, 2)).toBe('rgba(225, 29, 72, 1)')
    expect(marketColorWithAlpha('currentColor', 0.5)).toBe('currentColor')
  })

  it('persists manual choices and lets automatic mode react to language changes', async () => {
    const user = userEvent.setup()
    render(<MarketColorProvider><Probe /></MarketColorProvider>)

    expect(screen.getByTestId('effective').textContent).toBe('red-up')
    expect(document.documentElement.dataset.marketColorScheme).toBe('red-up')

    await user.click(screen.getByRole('button', { name: 'green-up' }))
    expect(window.localStorage.getItem(MARKET_COLOR_STORAGE_KEY)).toBe('green-up')
    expect(screen.getByTestId('effective').textContent).toBe('green-up')

    await act(async () => {
      await changeLocale('en-US')
    })
    expect(screen.getByTestId('effective').textContent).toBe('green-up')

    await user.click(screen.getByRole('button', { name: 'auto' }))
    expect(screen.getByTestId('preference').textContent).toBe('auto')
    expect(screen.getByTestId('effective').textContent).toBe('green-up')

    await act(async () => {
      await changeLocale('zh-CN')
    })
    expect(screen.getByTestId('effective').textContent).toBe('red-up')
    expect(document.documentElement.dataset.marketColorScheme).toBe('red-up')
  })

  it('syncs a preference changed in another browser tab', async () => {
    render(<MarketColorProvider><Probe /></MarketColorProvider>)

    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: MARKET_COLOR_STORAGE_KEY,
        newValue: 'green-up',
      }))
    })

    expect(screen.getByTestId('preference').textContent).toBe('green-up')
    expect(screen.getByTestId('effective').textContent).toBe('green-up')
    expect(document.documentElement.dataset.marketColorScheme).toBe('green-up')
  })
})
