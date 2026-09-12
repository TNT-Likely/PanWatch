import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import AssistantOpenBridge from '@/components/AssistantOpenBridge'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

describe('AssistantOpenBridge', () => {
  it('translates the stock insight event into assistant route state', async () => {
    render(
      <MemoryRouter initialEntries={['/portfolio']}>
        <AssistantOpenBridge />
        <LocationProbe />
      </MemoryRouter>,
    )

    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
      detail: { symbol: '600519', market: 'CN', stockName: '贵州茅台', pageContext: '行情上下文' },
    }))

    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant'))
  })
})
