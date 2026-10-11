import { afterEach, describe, expect, it } from 'vitest'
import { getBasePath, publicUrl } from '../../packages/api/src/base-path'

function setBasePath(value: string) {
  const meta = document.createElement('meta')
  meta.name = 'panwatch-base-path'
  meta.content = value
  document.head.append(meta)
}

afterEach(() => {
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
})

it('keeps existing root deployments unchanged without server metadata', () => {
  expect(getBasePath()).toBe('')
  expect(publicUrl('/api/auth/status')).toBe('/api/auth/status')
})

describe.each(['', '/panwatch', '/apps/panwatch'])('public URL at %s', prefix => {
  it('uses only trusted server metadata and retains the app-local path', () => {
    setBasePath(prefix)
    expect(getBasePath()).toBe(prefix)
    expect(publicUrl('/login')).toBe(`${prefix}/login`)
    expect(publicUrl('/api/report?symbol=SHOP.TO')).toBe(`${prefix}/api/report?symbol=SHOP.TO`)
  })
})

it.each(['//host', '/a//b', '/a/..', '/a/%2f', '/a\\b', '/a?b', '/a#b', 'https://host'])('rejects unsafe server base path %s', value => {
  setBasePath(value)
  expect(getBasePath).toThrow()
})

it.each(['https://host', '//host', '/\\host'])('rejects non-local destination %s', value => {
  expect(() => publicUrl(value)).toThrow()
})
