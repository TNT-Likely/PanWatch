import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@/i18n'
import ResearchConfigDialog from '@/components/ResearchConfigDialog'
import { ResearchProcessGraph } from '@/components/ResearchProcessGraph'
import { fetchAPI } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))
const metadata = { default_template: 'intraday_fast', templates: [
  { id: 'intraday_fast', graph: 'intraday_fast', allowed_specialists: ['technical', 'events'], defaults: { specialists: ['technical', 'events'], debate: false, debate_on: 'always', trigger_mode: 'changes', max_feedback: 0, max_attempts: 6, max_tools: 2, concurrency: 2, max_total_tokens: 80000, max_output_tokens: 1536, deadline_seconds: 45 } },
  { id: 'custom_research', graph: 'research', allowed_specialists: ['technical', 'flow', 'events'], defaults: { specialists: ['technical'], debate: false, debate_on: 'always', trigger_mode: 'changes', max_feedback: 0, max_attempts: 10, max_tools: 2, concurrency: 2, max_total_tokens: 250000, max_output_tokens: 4096, deadline_seconds: 45 } },
] }
const agent = { name: 'intraday_monitor', config: { unrelated: 'preserved' } }
beforeEach(() => { vi.mocked(fetchAPI).mockReset(); vi.mocked(fetchAPI).mockImplementation(async (path) => path.includes('research-templates') ? structuredClone(metadata) : {}) })
afterEach(cleanup)

async function open() {
  const onClose = vi.fn(), onSaved = vi.fn()
  render(<ResearchConfigDialog agent={agent} onClose={onClose} onSaved={onSaved} />)
  await screen.findByRole('switch', { name: '事件分析' })
  return { onClose, onSaved }
}

describe('validated research graph configuration', () => {
  it('saves selected specialists and per-run caps while preserving other agent settings', async () => {
    const { onSaved, onClose } = await open()
    fireEvent.click(screen.getByRole('switch', { name: '事件分析' }))
    fireEvent.change(screen.getByLabelText('整轮模型尝试上限'), { target: { value: '8' } })
    fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
    const call = vi.mocked(fetchAPI).mock.calls.find(([, options]) => options?.method === 'PUT')!
    const body = JSON.parse(String(call[1]?.body))
    expect(body.config.unrelated).toBe('preserved')
    expect(body.config.research_graph.template).toBe('intraday_fast')
    expect(body.config.research_graph.specialists).toEqual(['technical'])
    expect(body.config.research_graph.max_attempts).toBe(8)
    expect(body.config.research_graph.debate).toBe(false)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('switches to the complete graph using server defaults and previews its mandatory review stages', async () => {
    await open()
    fireEvent.keyDown(screen.getByRole('combobox', { name: '图模板' }), { key: 'ArrowDown' })
    fireEvent.click(await screen.findByRole('option', { name: '完整研究图（自定义）' }))
    expect((screen.getByLabelText('整轮模型尝试上限') as HTMLInputElement).value).toBe('10')
    expect(within(screen.getByRole('img', { name: '编排结构预览' })).getByText(/研究裁定/)).toBeTruthy()
    expect(screen.getByRole('combobox', { name: '牛熊论证' })).toBeTruthy()
  })

  it('does not save an empty graph or mutate configuration on cancel', async () => {
    const { onClose } = await open()
    fireEvent.click(screen.getByRole('switch', { name: '技术分析' }))
    fireEvent.click(screen.getByRole('switch', { name: '事件分析' }))
    expect((screen.getByRole('button', { name: '保存', exact: true }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '取消', exact: true }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(vi.mocked(fetchAPI).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0)
  })

  it('keeps saving disabled when template discovery fails', async () => {
    vi.mocked(fetchAPI).mockRejectedValue(new Error('offline'))
    render(<ResearchConfigDialog agent={agent} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect((screen.getByRole('button', { name: '保存', exact: true }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('labels deterministic nodes separately and selects the fast decision report by default', () => {
    render(<ResearchProcessGraph items={[
      { role: 'gate', title: 'gate', content: 'New signal', kind: 'rule' },
      { role: 'technical', title: 'technical', content: 'Evidence-backed technicals', kind: 'model' },
      { role: 'strategy', title: 'proposal', content: 'Fast conditional decision', kind: 'model' },
      { role: 'execution', title: 'rules', content: 'Execution guard', kind: 'rule' },
    ]} />)
    expect(screen.getByText('Fast conditional decision')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /执行校验/ }))
    expect(screen.getByText('规则节点 · 未调用模型')).toBeTruthy()
    expect(screen.getByText('Execution guard')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /研究裁定/ })).toBeNull()
  })
})
