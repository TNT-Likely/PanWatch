import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode, useState } from 'react'
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { chatApi, tradingAgentsApi, type DeepAnalysisResult, type HistoryComparisonResponse } from '@panwatch/api'
import AnalysisDetailPage from '@/pages/AnalysisDetail'
import AssistantPage from '@/pages/Assistant'
import { AssistantMarkdown } from '@/components/assistant/AssistantMarkdown'

vi.mock('@panwatch/api', async original => ({
  ...await original<typeof import('@panwatch/api')>(),
  tradingAgentsApi: {
    getAnalysisByDate: vi.fn(), getHistoryComparison: vi.fn(), downloadAnalysisPdf: vi.fn(),
  },
  chatApi: {
    listConversations: vi.fn(), getConversation: vi.fn(),
    getSuggestedQuestions: vi.fn().mockResolvedValue({ questions: [] }),
  },
}))
vi.mock('@/components/ShareCardModal', () => ({ default: () => null }))

const source = (label: string) => [
  '## Overview',
  '### Evidence',
  '#### Details',
  `${label} claim.[^1] The same source again.[^1]`,
  `[^1]: ${label} unique source.`,
].join('\n\n')
const result = {
  agent_name: 'tradingagents', title: 'Investment report', content: 'Report',
  analysis_date: '2026-10-09', timestamp: '2026-10-09',
  raw_data: {
    final_decision: source('Decision'),
    analyst_reports: { market: source('Market'), news: source('News') },
  },
} as DeepAnalysisResult

let base: HTMLBaseElement
beforeEach(() => {
  base = document.createElement('base')
  document.head.append(base)
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} })
  vi.mocked(tradingAgentsApi.getAnalysisByDate).mockResolvedValue(result)
  vi.mocked(tradingAgentsApi.getHistoryComparison).mockResolvedValue({ items: [], stats: {} } as HistoryComparisonResponse)
  vi.mocked(tradingAgentsApi.downloadAnalysisPdf).mockResolvedValue()
  const conversation = { id: 9, title: 'Footnote conversation', created_at: '2026-10-09' }
  vi.mocked(chatApi.listConversations).mockResolvedValue([conversation])
  vi.mocked(chatApi.getConversation).mockResolvedValue({
    conversation,
    messages: ['First', 'Second'].map((label, index) => ({
      id: 91 + index, role: 'assistant', content: source(label), created_at: '2026-10-09',
    })),
  })
})
afterEach(() => {
  cleanup()
  base.remove()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  localStorage.removeItem('panwatch_toc_show_sub')
  window.history.replaceState(null, '', '/')
})

function footnoteIds(root: ParentNode) {
  return Array.from(root.querySelectorAll<HTMLElement>('[data-footnote-ref], [data-footnotes] [id]'), node => node.id)
}

function expectUniqueIds() {
  const ids = Array.from(document.querySelectorAll('[id]'), node => node.id)
  expect(new Set(ids).size).toBe(ids.length)
}

function expectLocalFootnotes(root: HTMLElement, label: string, documentUrl: string) {
  const refs = Array.from(root.querySelectorAll<HTMLAnchorElement>('a[data-footnote-ref]'))
  const backs = Array.from(root.querySelectorAll<HTMLAnchorElement>('a[data-footnote-backref]'))
  expect(refs).toHaveLength(2)
  expect(backs).toHaveLength(2)
  const description = document.getElementById(refs[0].getAttribute('aria-describedby')!)!
  expect(description).toBeTruthy()
  expect(root.contains(description)).toBe(true)
  expect(description.tagName).toBe('H2')
  expect(description.classList.contains('sr-only')).toBe(true)
  expect(description.textContent).toBe('Footnotes')
  const note = document.getElementById(new URL(refs[0].href).hash.slice(1))!
  expect(note).toBeTruthy()
  expect(root.contains(note)).toBe(true)
  expect(note.textContent).toContain(`${label} unique source.`)
  expect(note.id).toMatch(/^user-content-.+-fn-1$/)
  for (const [index, ref] of refs.entries()) {
    expect(ref.href).toBe(`${documentUrl}#${note.id}`)
    expect(ref.getAttribute('aria-describedby')).toBe(description.id)
    expect(document.getElementById(ref.id)).toBe(ref)
    expect(backs[index].href).toBe(`${documentUrl}#${ref.id}`)
    expect(backs[index].getAttribute('aria-label')).toBe(`Back to reference 1${index ? '-2' : ''}`)
    expect(backs[index].classList.contains('data-footnote-backref')).toBe(true)
  }
  return { refs, backs, note, description }
}

function renderReport(prefix: string) {
  let navigate!: ReturnType<typeof useNavigate>
  function Navigation() {
    navigate = useNavigate()
    return <Routes>
      <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />
      <Route path="/history" element={<p>History page</p>} />
    </Routes>
  }
  render(<StrictMode><BrowserRouter basename={prefix || '/'}><Navigation /></BrowserRouter></StrictMode>)
  return (to: string | number) => typeof to === 'number' ? navigate(to) : navigate(to)
}

describe.each(['', '/panwatch', '/apps/panwatch'])('production report footnotes at "%s"', prefix => {
  const path = '/analysis/AAPL/2026-10-09'
  beforeEach(() => {
    base.href = `${prefix}/`
    window.history.replaceState(null, '', `${prefix}${path}?view=full`)
  })

  it('keeps every repeated citation, backlink and hidden label in its own report section', async () => {
    renderReport(prefix)
    await screen.findByRole('heading', { name: 'Investment report' })
    const current = `${window.location.origin}${prefix}${path}?view=full`
    for (const [id, label] of [['decision', 'Decision'], ['market', 'Market'], ['news', 'News']]) {
      const section = document.getElementById(`sec-${id}`)!
      const { description } = expectLocalFootnotes(section, label, current)
      expect(description.classList.contains('scroll-mt-24')).toBe(true)
      expect(description.hasAttribute('node')).toBe(false)
      for (const [tag, heading] of [['H2', 'overview'], ['H3', 'evidence'], ['H4', 'details']]) {
        const target = document.getElementById(`h-${id}-${heading}`)!
        expect(target.tagName).toBe(tag)
        expect(target.classList.contains('scroll-mt-24')).toBe(true)
        expect(section.contains(target)).toBe(true)
      }
    }
    expectUniqueIds()
    expect(document.getElementById('footnote-label')).toBeNull()
    expect(document.getElementById('h-decision-footnotes')).toBeNull()
    const scroll = vi.spyOn(document.getElementById('h-market-evidence')!, 'scrollIntoView')
    await userEvent.setup().click(screen.getAllByRole('button', { name: 'Evidence', exact: true })[1])
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth' })
  })

  it('retains identities across report rerenders, query navigation and Back', async () => {
    const navigate = renderReport(prefix)
    await screen.findByRole('heading', { name: 'Investment report' })
    const ids = footnoteIds(document)
    await userEvent.setup().click(screen.getByRole('switch'))
    expect(footnoteIds(document)).toEqual(ids)
    await act(async () => navigate('?view=summary#previous'))
    expect(footnoteIds(document)).toEqual(ids)
    expectLocalFootnotes(document.getElementById('sec-market')!, 'Market', `${window.location.origin}${prefix}${path}?view=summary`)
    await act(async () => navigate(-1))
    await waitFor(() => expect(window.location.search).toBe('?view=full'))
    expect(footnoteIds(document)).toEqual(ids)
    expectLocalFootnotes(document.getElementById('sec-market')!, 'Market', `${window.location.origin}${prefix}${path}?view=full`)
    expectUniqueIds()
  })

  it('allows native citation and repeated-reference backlink activation in the correct section', async () => {
    renderReport(prefix)
    await screen.findByRole('heading', { name: 'Investment report' })
    const current = `${window.location.origin}${prefix}${path}?view=full`
    const { refs, backs, note } = expectLocalFootnotes(document.getElementById('sec-news')!, 'News', current)
    const canceled: boolean[] = []
    const onClick = (event: Event) => { canceled.push(event.defaultPrevented) }
    document.addEventListener('click', onClick)
    try {
      const user = userEvent.setup()
      await user.click(refs[1])
      await waitFor(() => expect(window.location.href).toBe(`${current}#${note.id}`))
      await user.click(backs[1])
      await waitFor(() => expect(window.location.href).toBe(`${current}#${refs[1].id}`))
      expect(canceled).toEqual([false, false])
    } finally {
      document.removeEventListener('click', onClick)
    }
  })

  it('restores native footnote targets after route teardown, Back and a fresh deep-link mount', async () => {
    const navigate = renderReport(prefix)
    await screen.findByRole('heading', { name: 'Investment report' })
    const ids = footnoteIds(document)
    const current = `${window.location.origin}${prefix}${path}?view=full`
    const { refs } = expectLocalFootnotes(document.getElementById('sec-market')!, 'Market', current)
    await userEvent.setup().click(refs[0])
    await waitFor(() => expect(window.location.href).toBe(refs[0].href))
    const originalHash = window.location.hash
    await act(async () => navigate('/history'))
    await screen.findByText('History page')
    expect(document.getElementById(originalHash.slice(1))).toBeNull()
    await act(async () => navigate(-1))
    await screen.findByRole('heading', { name: 'Investment report' })
    expect(window.location.hash).toBe(originalHash)
    expect(document.getElementById(originalHash.slice(1))?.textContent).toContain('Market unique source.')
    expect(footnoteIds(document)).toEqual(ids)
    cleanup()
    renderReport(prefix)
    await screen.findByRole('heading', { name: 'Investment report' })
    expect(window.location.hash).toBe(originalHash)
    expect(document.getElementById(originalHash.slice(1))?.textContent).toContain('Market unique source.')
    expect(footnoteIds(document)).toEqual(ids)
    expectUniqueIds()
  })
})

describe.each(['', '/panwatch', '/apps/panwatch'])('production assistant footnotes at "%s"', prefix => {
  beforeEach(() => {
    base.href = `${prefix}/`
    window.history.replaceState(null, '', `${prefix}/assistant/9?view=chat`)
  })

  it('isolates keyed messages and retains identity while streaming, reordering and navigating', async () => {
    let setMessages!: ReturnType<typeof useState<string[]>>[1]
    let setStreaming!: ReturnType<typeof useState<boolean>>[1]
    let setTail!: ReturnType<typeof useState<string>>[1]
    let navigate!: ReturnType<typeof useNavigate>
    function Conversation() {
      const [messages, updateMessages] = useState(['First', 'Second'])
      const [streaming, updateStreaming] = useState(true)
      const [tail, updateTail] = useState('')
      setMessages = updateMessages
      setStreaming = updateStreaming
      setTail = updateTail
      navigate = useNavigate()
      return messages.map(label => <div key={label} id={`message-${label}`}>
        <AssistantMarkdown content={source(label) + (label === 'Second' ? tail : '')} streaming={label === 'Second' && streaming} />
      </div>)
    }
    render(<StrictMode><BrowserRouter basename={prefix || '/'}><Conversation /></BrowserRouter></StrictMode>)
    const first = document.getElementById('message-First')!
    const second = document.getElementById('message-Second')!
    const ids = { first: footnoteIds(first), second: footnoteIds(second) }
    let current = `${window.location.origin}${prefix}/assistant/9?view=chat`
    expectLocalFootnotes(first, 'First', current)
    expectLocalFootnotes(second, 'Second', current)
    expectUniqueIds()
    await act(async () => setTail('\n\nStill streaming.\n\n```text\npartial code'))
    expect(second.querySelector('pre code')?.textContent).toContain('partial code')
    expect(footnoteIds(second)).toEqual(ids.second)
    await act(async () => { setTail('\n\nFinished.'); setStreaming(false); setMessages(['Second', 'First', 'Third']) })
    expect(footnoteIds(first)).toEqual(ids.first)
    expect(footnoteIds(second)).toEqual(ids.second)
    expectLocalFootnotes(document.getElementById('message-Third')!, 'Third', current)
    expectUniqueIds()
    await act(async () => navigate('/assistant/10?view=research#old'))
    current = `${window.location.origin}${prefix}/assistant/10?view=research`
    expectLocalFootnotes(first, 'First', current)
    expectLocalFootnotes(second, 'Second', current)
    expect(footnoteIds(first)).toEqual(ids.first)
    expect(footnoteIds(second)).toEqual(ids.second)
    await act(async () => navigate(-1))
    await waitFor(() => expect(window.location.pathname).toBe(`${prefix}/assistant/9`))
    expectLocalFootnotes(second, 'Second', `${window.location.origin}${prefix}/assistant/9?view=chat`)
    expect(footnoteIds(second)).toEqual(ids.second)
    await act(async () => setMessages(['Second', 'Third']))
    expect(footnoteIds(second)).toEqual(ids.second)
    expectUniqueIds()
  })

  it('keeps unsafe URLs and raw HTML inert while namespacing clobber-prone footnote labels', () => {
    const content = [
      'Claim.[^location] Another.[^constructor]',
      '[^location]: [Script](javascript:alert%281%29)',
      '[^constructor]: [Data](data:text/html,unsafe)',
      '<script>window.markdownExecuted = true</script>',
      '<h2 id="footnote-label">Forged label</h2>',
      '[Author fragment](#footnote-label)',
    ].join('\n\n')
    render(<>
      <AssistantMarkdown documentId={'message:/你好#<script>"'} content={content} />
      <AssistantMarkdown documentId={'message-你好-script'} content={content} />
    </>)
    expect(document.querySelector('script')).toBeNull()
    expect(document.getElementById('footnote-label')).toBeNull()
    expect(document.getElementById('location')).toBeNull()
    expect(document.getElementById('constructor')).toBeNull()
    for (const label of ['Script', 'Data']) {
      for (const link of screen.getAllByText(label, { selector: 'a' })) expect(link.getAttribute('href')).toBe('')
    }
    for (const link of screen.getAllByRole('link', { name: 'Author fragment' })) {
      expect(link.getAttribute('href')).toBe(`${window.location.origin}${prefix}/assistant/9?view=chat#footnote-label`)
    }
    for (const ref of document.querySelectorAll<HTMLAnchorElement>('[data-footnote-ref]')) {
      const note = document.getElementById(new URL(ref.href).hash.slice(1))!
      expect(note.id).toMatch(/^user-content-.+-fn-(location|constructor)$/)
      expect(note.id).toMatch(/^[a-z0-9-]+$/)
      expect(document.getElementById(ref.getAttribute('aria-describedby')!)?.classList.contains('sr-only')).toBe(true)
    }
    expectUniqueIds()
  })

  it('restores persisted message citations after actual assistant-page teardown and deep-link remount', async () => {
    let navigate!: ReturnType<typeof useNavigate>
    function Navigation() {
      navigate = useNavigate()
      return <Routes>
        <Route path="/assistant/:conversationId" element={<AssistantPage />} />
        <Route path="/history" element={<p>History page</p>} />
      </Routes>
    }
    const mount = () => render(<BrowserRouter basename={prefix || '/'}><Navigation /></BrowserRouter>)
    mount()
    await screen.findByText(/Second unique source\./)
    const ids = footnoteIds(document)
    const ref = document.querySelectorAll<HTMLAnchorElement>('[data-footnote-ref]')[2]
    expectLocalFootnotes(ref.closest('div.prose')!, 'Second', `${window.location.origin}${prefix}/assistant/9?view=chat`)
    await userEvent.setup().click(ref)
    await waitFor(() => expect(window.location.href).toBe(ref.href))
    const originalHash = window.location.hash
    await act(async () => navigate('/history'))
    await screen.findByText('History page')
    expect(document.getElementById(originalHash.slice(1))).toBeNull()
    await act(async () => navigate(-1))
    await screen.findByText(/Second unique source\./)
    expect(window.location.hash).toBe(originalHash)
    expect(document.getElementById(originalHash.slice(1))?.textContent).toContain('Second unique source.')
    expect(footnoteIds(document)).toEqual(ids)
    cleanup()
    mount()
    await screen.findByText(/Second unique source\./)
    expect(window.location.hash).toBe(originalHash)
    expect(document.getElementById(originalHash.slice(1))?.textContent).toContain('Second unique source.')
    expect(footnoteIds(document)).toEqual(ids)
    expectUniqueIds()
  })
})
