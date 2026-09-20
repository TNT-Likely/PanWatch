import { describe, expect, it } from 'vitest'

import { pageLoaders, resolveRouteKey } from '@/router/page-loaders'

describe('route page loaders', () => {
  it('registers every route page exactly once', () => {
    expect(Object.keys(pageLoaders)).toEqual([
      'login',
      'dashboard',
      'stocks',
      'opportunities',
      'paperTrading',
      'assistant',
      'alerts',
      'agents',
      'evaluations',
      'history',
      'dataSources',
      'settings',
      'analysisDetail',
    ])
  })

  it('resolves both assistant routes to the same page loader', () => {
    expect(resolveRouteKey('/assistant')).toBe('assistant')
    expect(resolveRouteKey('/assistant/123')).toBe('assistant')
  })

  it('does not preload an unknown route', () => {
    expect(resolveRouteKey('/missing')).toBeNull()
  })
})
