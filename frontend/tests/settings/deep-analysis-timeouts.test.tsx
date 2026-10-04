import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import AgentsPage from '@/pages/Agents'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async original => ({ ...await original<typeof import('@panwatch/api')>(), fetchAPI: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function openConfig(config: Record<string, unknown>) {
  const agent = { id: 1, name: 'tradingagents', display_name: 'TradingAgents 深度分析', enabled: true,
    schedule: '', execution_mode: 'single', ai_model_id: null, notify_channel_ids: [], config }
  vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
    if (path === '/agents/tradingagents' && options?.method === 'PUT') {
      agent.config = JSON.parse(String(options.body)).config
      return agent
    }
    if (path === '/agents') return [agent]
    if (path === '/stocks' || path === '/services' || path === '/channels') return []
    return new Promise<never>(() => {})
  })
  render(<MemoryRouter><MarketColorProvider><AgentsPage /></MarketColorProvider></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: '深度配置' }))
  return within(await screen.findByRole('dialog'))
}

function savedConfig() {
  const call = vi.mocked(fetchAPI).mock.calls.find(([path, options]) => path === '/agents/tradingagents' && options?.method === 'PUT')
  expect(call).toBeTruthy()
  return JSON.parse(String(call![1]!.body)).config
}

it('shows and saves all timeout defaults for older configurations', async () => {
  const dialog = await openConfig({ debate_rounds: 1, llm_max_tokens: 4096 })
  expect((dialog.getByLabelText('整轮分析超时（分钟）') as HTMLInputElement).value).toBe('30')
  expect((dialog.getByLabelText('单次模型请求超时（秒）') as HTMLInputElement).value).toBe('300')
  expect((dialog.getByLabelText('单个数据源采集超时（秒）') as HTMLInputElement).value).toBe('45')
  await userEvent.click(dialog.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(savedConfig()).toEqual({ debate_rounds: 1,
    timeout_minutes: 30, llm_timeout_seconds: 300, collection_timeout_seconds: 45 }))
})

it('lets users replace each saved timeout and retains unrelated settings', async () => {
  const dialog = await openConfig({ timeout_minutes: 15, llm_timeout_seconds: 300,
    collection_timeout_seconds: 45, debate_rounds: 2, cache_ttl_hours: 6 })
  for (const [label, value] of [
    ['整轮分析超时（分钟）', '45'], ['单次模型请求超时（秒）', '600'], ['单个数据源采集超时（秒）', '90'],
  ]) {
    const input = dialog.getByLabelText(label)
    await userEvent.clear(input)
    await userEvent.type(input, value)
  }
  await userEvent.click(dialog.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(savedConfig()).toEqual({ timeout_minutes: 45, llm_timeout_seconds: 600,
    collection_timeout_seconds: 90, debate_rounds: 2, cache_ttl_hours: 6 }))
})

it('keeps empty or out-of-range inputs from saving invalid timeouts', async () => {
  const dialog = await openConfig({})
  fireEvent.change(dialog.getByLabelText('整轮分析超时（分钟）'), { target: { value: '1000' } })
  fireEvent.change(dialog.getByLabelText('单次模型请求超时（秒）'), { target: { value: '' } })
  fireEvent.change(dialog.getByLabelText('单个数据源采集超时（秒）'), { target: { value: '0' } })
  await userEvent.click(dialog.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(savedConfig()).toEqual({ timeout_minutes: 60, llm_timeout_seconds: 300,
    collection_timeout_seconds: 5 }))
})

it('closes on Escape without saving and returns focus to the opener', async () => {
  await openConfig({ timeout_minutes: 30 })
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(vi.mocked(fetchAPI).mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '深度配置' }))
})
