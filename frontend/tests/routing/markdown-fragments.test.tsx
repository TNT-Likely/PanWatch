import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import remarkGfm from 'remark-gfm'
import Markdown from '@panwatch/base-ui/components/markdown'
import { AssistantMarkdown } from '@/components/assistant/AssistantMarkdown'

const content = 'A claim.[^source]\n\n[Details](#details)\n\n[^source]: A supporting source.'
let base: HTMLBaseElement

beforeEach(() => {
  base = document.createElement('base')
  document.head.append(base)
})

afterEach(() => {
  cleanup()
  base.remove()
  window.history.replaceState(null, '', '/')
})

function expectFootnoteLinks(documentUrl: string) {
  const ref = document.querySelector<HTMLAnchorElement>('a[data-footnote-ref]')!
  const back = document.querySelector<HTMLAnchorElement>('a[data-footnote-backref]')!
  expect(ref).toBeTruthy()
  expect(back).toBeTruthy()
  const prefix = ref.id.slice(0, -'fnref-source'.length)
  expect(prefix).toMatch(/^user-content-.+-$/)
  expect(ref.href).toBe(`${documentUrl}#${prefix}fn-source`)
  expect(back.href).toBe(`${documentUrl}#${ref.id}`)
  expect(document.getElementById(`${prefix}fn-source`)).toBeTruthy()
  expect(document.getElementById(ref.id)).toBe(ref)
  expect(ref.getAttribute('aria-describedby')).toBe(`${prefix}footnote-label`)
  expect(document.getElementById(`${prefix}footnote-label`)).toBeTruthy()
  expect(back.getAttribute('aria-label')).toBe('Back to reference 1')
  expect(back.classList.contains('data-footnote-backref')).toBe(true)
  return { ref, back }
}

describe.each(['', '/panwatch', '/apps/panwatch'])('Markdown fragments at public base "%s"', prefix => {
  beforeEach(() => {
    base.setAttribute('href', `${prefix}/`)
    window.history.replaceState(null, '', `${prefix}/assistant/123?view=research&query=a%23b#previous`)
  })

  it('keeps actual GFM footnotes, backrefs and ordinary fragments on the current document', () => {
    render(<AssistantMarkdown content={content} />)
    const current = `${window.location.origin}${prefix}/assistant/123?view=research&query=a%23b`
    expectFootnoteLinks(current)
    expect((screen.getByRole('link', { name: 'Details' }) as HTMLAnchorElement).href).toBe(`${current}#details`)
    expect(base.getAttribute('href')).toBe(`${prefix}/`)
  })

  it('allows native same-document footnote and backlink activation without canceling events', async () => {
    const user = userEvent.setup()
    render(<BrowserRouter basename={prefix || '/'}><AssistantMarkdown content={content} /></BrowserRouter>)
    const current = `${window.location.origin}${prefix}/assistant/123?view=research&query=a%23b`
    const { ref, back } = expectFootnoteLinks(current)
    const canceled: boolean[] = []
    const captureDefault = (event: Event) => { canceled.push(event.defaultPrevented) }
    document.addEventListener('click', captureDefault)
    try {
      await user.click(ref)
      await waitFor(() => expect(window.location.href).toBe(ref.href))
      await user.click(back)
      await waitFor(() => expect(window.location.href).toBe(back.href))
      expect(canceled).toEqual([false, false])
    } finally {
      document.removeEventListener('click', captureDefault)
    }
  })

  it('updates unchanged memoized assistant Markdown after pathname and query navigation', async () => {
    let navigate!: ReturnType<typeof useNavigate>
    function Navigation() {
      navigate = useNavigate()
      return <AssistantMarkdown content={content} />
    }
    render(<BrowserRouter basename={prefix || '/'}><Navigation /></BrowserRouter>)
    expectFootnoteLinks(`${window.location.origin}${prefix}/assistant/123?view=research&query=a%23b`)

    await act(async () => navigate('/assistant/456?view=new#old-fragment'))
    expectFootnoteLinks(`${window.location.origin}${prefix}/assistant/456?view=new`)
    await act(async () => navigate('?view=latest&query=a%2Fb#another-fragment'))
    expectFootnoteLinks(`${window.location.origin}${prefix}/assistant/456?view=latest&query=a%2Fb`)
    await act(async () => navigate(-1))
    await waitFor(() => expectFootnoteLinks(`${window.location.origin}${prefix}/assistant/456?view=new`))
  })
})


describe.each(['', '/panwatch', '/apps/panwatch'])('shared report Markdown at public base "%s"', prefix => {
  beforeEach(() => { base.setAttribute('href', `${prefix}/`) })

  it.each(['/analysis/AAPL/2026-10-01', '/history', '/'])('preserves report heading components and native hrefs on %s', path => {
    window.history.replaceState(null, '', `${prefix}${path}?view=full#previous`)
    render(<Markdown remarkPlugins={[remarkGfm]} components={{
      h3: ({ node: _node, ...props }) => <h3 {...props} id="details" />,
    }}>{`### Report section\n\n${content}`}</Markdown>)
    const current = `${window.location.origin}${prefix}${path}?view=full`
    expectFootnoteLinks(current)
    const details = screen.getByRole('link', { name: 'Details' }) as HTMLAnchorElement
    expect(details.getAttribute('href')).toBe(`${current}#details`)
    expect(document.getElementById(new URL(details.href).hash.slice(1))?.textContent).toBe('Report section')
    expect(base.getAttribute('href')).toBe(`${prefix}/`)
  })

  it('keeps external, relative, query-only links and image sources unchanged', () => {
    window.history.replaceState(null, '', `${prefix}/analysis/RY/2026-10-01?view=full#previous`)
    const links = [
      ['External', 'https://example.org/report?day=1#source'],
      ['Other origin', '//example.org/report#source'],
      ['Email', 'mailto:analyst@example.org'],
      ['Relative', 'reports/source#details'],
      ['Parent', '../source#details'],
      ['Root', '/help#details'],
      ['Query', '?view=summary#details'],
    ]
    render(<Markdown>{`${links.map(([label, href]) => `[${label}](${href})`).join('\n\n')}\n\n![Chart](#chart)`}</Markdown>)
    for (const [label, href] of links) {
      const anchor = screen.getByRole('link', { name: label }) as HTMLAnchorElement
      expect(anchor.getAttribute('href')).toBe(href)
      expect(anchor.href).toBe(new URL(href, document.baseURI).href)
    }
    expect(screen.getByAltText('Chart').getAttribute('src')).toBe('#chart')
  })

  it('retains the default unsafe-protocol filter and supports empty and encoded fragments', () => {
    window.history.replaceState(null, '', `${prefix}/analysis/RY/2026-10-01?view=full#previous`)
    render(<Markdown>{[
      '[Script](javascript:alert%281%29)',
      '[Encoded script](jav&#x61;script:alert%281%29)',
      '[VBScript](vbscript:msgbox%281%29)',
      '[Data](data:text/html,unsafe)',
      '[Top](#)',
      '[Encoded fragment](#section%20one)',
    ].join('\n\n')}</Markdown>)
    for (const label of ['Script', 'Encoded script', 'VBScript', 'Data']) {
      expect(screen.getByText(label, { selector: 'a' }).getAttribute('href')).toBe('')
    }
    const current = `${window.location.origin}${prefix}/analysis/RY/2026-10-01?view=full`
    expect(screen.getByRole('link', { name: 'Top' }).getAttribute('href')).toBe(`${current}#`)
    expect(screen.getByRole('link', { name: 'Encoded fragment' }).getAttribute('href')).toBe(`${current}#section%20one`)
  })
})

it('routes every application and shared UI Markdown renderer through the common component', () => {
  const sourceFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = resolve(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(path) ? [path] : []
  })
  const frontend = process.cwd()
  const directImports = ['src', 'packages/base-ui/src', 'packages/biz-ui/src']
    .flatMap(directory => sourceFiles(resolve(frontend, directory)))
    .filter(path => /(?:from\s*|import\s*\()(['"])react-markdown\1/.test(readFileSync(path, 'utf8')))
  expect(directImports).toEqual([resolve(frontend, 'packages/base-ui/src/components/markdown.tsx')])
})
