import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, cleanup } from '@testing-library/react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'
import { getBasePath, publicUrl } from '../../packages/api/src/base-path'

function setPrefix(prefix: string) {
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
  const meta = document.createElement('meta')
  meta.name = 'panwatch-base-path'; meta.content = prefix
  document.head.append(meta)
}

beforeEach(() => { vi.resetModules(); localStorage.clear() })
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks()
  setPrefix(''); window.history.replaceState(null, '', '/')
})

describe.each(['', '/panwatch', '/apps/panwatch'])('public prefix %s', prefix => {
  it('centralizes browser URLs without changing app-local Router destinations', () => {
    setPrefix(prefix)
    expect(getBasePath()).toBe(prefix)
    expect(publicUrl('/api/auth/status')).toBe(`${prefix}/api/auth/status`)
    expect(publicUrl('/login?next=%2Fassistant%2F3')).toBe(`${prefix}/login?next=%2Fassistant%2F3`)
    window.history.replaceState(null, '', `${prefix}/assistant/3`)
    render(<BrowserRouter basename={getBasePath() || '/'}><Routes>
      <Route path="/assistant/:id" element={<Link to="/agents">Agents</Link>} />
      <Route path="/agents" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<p>Sign in</p>} />
    </Routes></BrowserRouter>)
    expect(screen.getByRole('link').getAttribute('href')).toBe(`${prefix}/agents`)
    fireEvent.click(screen.getByRole('link'))
    expect(window.location.pathname).toBe(`${prefix}/login`)
    expect(screen.getByText('Sign in')).toBeTruthy()
  })

  it('uses the prefix for API requests and carries existing auth headers', async () => {
    setPrefix(prefix)
    localStorage.setItem('token', 'test-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({code: 0, data: {ok: true}})))
    vi.stubGlobal('fetch', fetchMock)
    const { fetchAPI } = await import('../../packages/api/src/client')
    await expect(fetchAPI('/auth/status')).resolves.toEqual({ok: true})
    expect(fetchMock).toHaveBeenCalledWith(`${prefix}/api/auth/status`, expect.objectContaining({
      headers: expect.objectContaining({Authorization: 'Bearer test-token'}),
    }))
  })

  it('uses the prefix for SSE and preserves resume headers and streamed events', async () => {
    setPrefix(prefix)
    localStorage.setItem('token', 'test-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response('id: 8\nevent: update\ndata: {"ok":true}\n\n', {
      headers: {'Content-Type': 'text/event-stream'},
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { readSSE } = await import('../../packages/api/src/sse')
    const onEvent = vi.fn()
    await expect(readSSE('/notifications/stream', {lastEventId: 7, onEvent})).resolves.toEqual({lastEventId: 8})
    expect(fetchMock).toHaveBeenCalledWith(`${prefix}/api/notifications/stream`, expect.objectContaining({
      headers: expect.objectContaining({Authorization: 'Bearer test-token', 'Last-Event-ID': '7'}),
    }))
    expect(onEvent).toHaveBeenCalledWith({id: 8, event: 'update', data: {ok: true}})
  })

  it('uses the prefix for the direct PDF download request', async () => {
    setPrefix(prefix)
    const fetchMock = vi.fn().mockResolvedValue(new Response('', {status: 404}))
    vi.stubGlobal('fetch', fetchMock)
    const { tradingAgentsApi } = await import('../../packages/api/src/tradingagents')
    await expect(tradingAgentsApi.downloadAnalysisPdf('SHOP.TO', '2026-10-11')).rejects.toThrow()
    expect(fetchMock.mock.calls[0][0]).toBe(`${prefix}/api/agents/tradingagents/analysis/pdf?stock_symbol=SHOP.TO&analysis_date=2026-10-11`)
  })

  it('keeps locale-specific manifest links within the prefix', async () => {
    setPrefix(prefix)
    const link = document.createElement('link'); link.rel = 'manifest'; document.head.append(link)
    const { changeLocale } = await import('../../src/i18n')
    await changeLocale('en-US')
    expect(link.getAttribute('href')).toBe(`${prefix}/manifest.json`)
    await changeLocale('zh-CN')
    expect(link.getAttribute('href')).toBe(`${prefix}/manifest.zh-CN.json`)
    link.remove()
  })
})

it.each(['//host', '/a//b', '/a/..', '/a/%2f', '/a\\b', '/a?b', '/a#b', '/a b', 'https://host', '/a\n'])('rejects unsafe runtime prefix %s', prefix => {
  setPrefix(prefix)
  expect(getBasePath).toThrow()
})

it.each(['https://host', '//host', '/\\host'])('rejects nonlocal browser path %s', path => {
  expect(() => publicUrl(path)).toThrow()
})
