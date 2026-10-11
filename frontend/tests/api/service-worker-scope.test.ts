import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { runInNewContext } from 'node:vm'
import { describe, expect, it, vi } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf8')
function worker(scope: string) {
  const listeners: Record<string, (event: any) => void> = {}
  const cache = {addAll: vi.fn().mockResolvedValue(undefined), put: vi.fn().mockResolvedValue(undefined), match: vi.fn().mockResolvedValue(undefined)}
  const prefix = new URL(scope).pathname
  const caches = {
    open: vi.fn().mockResolvedValue(cache),
    keys: vi.fn().mockResolvedValue([`panwatch:${prefix}:v3`, `panwatch:${prefix}:v4`, 'another-app-cache', 'panwatch:/other/:v2']),
    delete: vi.fn().mockResolvedValue(true),
  }
  const fetch = vi.fn().mockResolvedValue(new Response('ok'))
  const self = {registration: {scope}, addEventListener: (name: string, callback: any) => {listeners[name] = callback}, skipWaiting: vi.fn(), clients: {claim: vi.fn()}}
  runInNewContext(source, {self, caches, fetch, URL, Response})
  return {listeners, caches, cache, fetch}
}

describe.each(['/', '/panwatch/', '/apps/panwatch/'])('service worker scope %s', prefix => {
  it('installs prefixed assets and only cleans its own cache versions', async () => {
    const w = worker(`https://example.test${prefix}`)
    const promises: Promise<unknown>[] = []
    const event = {waitUntil: (p: Promise<unknown>) => promises.push(p)}
    w.listeners.install(event); w.listeners.activate(event)
    await Promise.all(promises)
    expect(w.cache.addAll).toHaveBeenCalledWith(['', 'manifest.json', 'manifest.zh-CN.json', 'icon-192.png', 'icon-512.png'].map(path => `https://example.test${prefix}${path}`))
    expect(w.caches.delete.mock.calls).toEqual([[`panwatch:${prefix}:v3`]])
  })

  it('does not intercept API/protocol/non-GET/foreign-origin requests', () => {
    const w = worker(`https://example.test${prefix}`)
    const paths = ['api/auth/status', 'api/notifications/stream', 'api', 'mcp', 'mcp/stream', 'docs', 'openapi.json', '.well-known/oauth-protected-resource', 'apps/panwatch/api/private', 'apps/panwatch/api/auth/status', 'apps/panwatch/mcp', 'apps/panwatch/assets/app.js', 'unrelated/private-page', 'login', 'assets/panwatch/assets/app.js', 'assets/panwatch/login']
    for (const path of paths) {
      const respondWith = vi.fn()
      w.listeners.fetch({request: new Request(`https://example.test${prefix}${path}`), respondWith})
      expect(respondWith).not.toHaveBeenCalled()
    }
    for (const request of [new Request(`https://example.test${prefix}login`, {method: 'POST'}), new Request(`https://example.test.evil${prefix}asset.js`), new Request(`https://other.test${prefix}asset.js`)]) {
      const respondWith = vi.fn(); w.listeners.fetch({request, respondWith}); expect(respondWith).not.toHaveBeenCalled()
    }
    if (prefix !== '/') {
      for (const path of ['/', '/api/auth/status', '/panwatch-other/asset.js']) {
        const respondWith = vi.fn(); w.listeners.fetch({request: new Request(`https://example.test${path}`), respondWith}); expect(respondWith).not.toHaveBeenCalled()
      }
    }
  })


  it('does not cache HTML or redirected responses behind flat asset URLs', async () => {
    const w = worker(`https://example.test${prefix}`)
    const request = new Request(`https://example.test${prefix}assets/panwatch.js`)
    for (const redirected of [false, true]) {
      const response = new Response('other app', {headers: {'Content-Type': redirected ? 'application/javascript' : 'text/html; charset=utf-8'}})
      Object.defineProperty(response, 'redirected', {value: redirected})
      w.fetch.mockResolvedValue(response)
      let pending: Promise<Response> | undefined
      const jobs: Promise<unknown>[] = []
      w.listeners.fetch({request, respondWith: (p: Promise<Response>) => {pending = p}, waitUntil: (p: Promise<unknown>) => jobs.push(p)})
      expect(await (await pending!).text()).toBe('other app')
      await Promise.all(jobs)
      expect(w.cache.put).not.toHaveBeenCalled()
    }
  })

  it('caches and falls back only inside this installation', async () => {
    const w = worker(`https://example.test${prefix}`)
    const request = new Request(`https://example.test${prefix}assets/app.js`)
    let response: Promise<Response> | undefined
    const jobs: Promise<unknown>[] = []
    const event = {request, respondWith: (p: Promise<Response>) => {response = p}, waitUntil: (p: Promise<unknown>) => jobs.push(p)}
    w.listeners.fetch(event)
    expect(await (await response!).text()).toBe('ok')
    await Promise.all(jobs)
    expect(w.caches.open).toHaveBeenCalledWith(`panwatch:${prefix}:v4`)
    expect(w.cache.put).toHaveBeenCalledWith(request, expect.any(Response))
    w.fetch.mockRejectedValue(new Error('offline'))
    w.cache.match.mockResolvedValue(new Response('cached'))
    w.listeners.fetch(event)
    expect(await (await response!).text()).toBe('cached')
  })
})
