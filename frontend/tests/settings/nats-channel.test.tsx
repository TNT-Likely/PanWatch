import { cleanup, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI, patsApi } from '@panwatch/api'
import SettingsPage from '@/pages/Settings'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import i18n from '@/i18n'
import { configuration as en } from '@/i18n/locales/en-US/configuration'
import { configuration as zh } from '@/i18n/locales/zh-CN/configuration'

vi.mock('@panwatch/api', async original => ({ ...await original<typeof import('@panwatch/api')>(), fetchAPI: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear() })

it.each([['en-US', en], ['zh-CN', zh]] as const)('creates a NATS channel with environment references and cancels cleanly in %s', async (language, resource) => {
  await i18n.changeLanguage(language)
  vi.spyOn(patsApi, 'list').mockResolvedValue({ items: [] })
  vi.mocked(fetchAPI).mockImplementation(async path => {
    if (path === '/settings' || path === '/channels') return []
    if (path === '/settings/avatar') return { value: '' }
    return new Promise<never>(() => {})
  })
  render(<MemoryRouter><MarketColorProvider><SettingsPage /></MarketColorProvider></MemoryRouter>)
  const user = userEvent.setup()
  const labels = resource.settingsPage
  await waitFor(() => expect(document.getElementById('sec-notify')).toBeTruthy())
  const section = document.getElementById('sec-notify')!
  await user.click(within(section).getByRole('button', { name: labels.notifications.add }))
  let dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('combobox'))
  await user.click(screen.getByRole('option', { name: 'NATS JetStream' }))
  expect(within(dialog).getByText(labels.channels.natsHint)).toBeTruthy()
  await user.type(within(dialog).getByPlaceholderText(labels.channels.channelNamePlaceholder), 'Bus')
  await user.type(within(dialog).getByPlaceholderText('tls://nats.example.net:4222'), 'tls://broker.example.net:4222')
  await user.type(within(dialog).getByPlaceholderText('panwatch.notifications.v1'), 'panwatch.notifications.v1')
  await user.type(within(dialog).getByPlaceholderText('PANWATCH_NOTIFICATIONS'), 'PANWATCH_NOTIFICATIONS')
  await user.type(within(dialog).getByPlaceholderText('PANWATCH_NATS_TOKEN'), 'MY_NATS_TOKEN')
  await user.click(within(dialog).getByRole('button', { name: labels.dialogs.create }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const request = vi.mocked(fetchAPI).mock.calls.find(([path, options]) => path === '/channels' && options?.method === 'POST')
  expect(request).toBeTruthy()
  expect(JSON.parse(String(request![1]!.body))).toEqual({ name: 'Bus', type: 'nats_jetstream', config: { server_url: 'tls://broker.example.net:4222', subject: 'panwatch.notifications.v1', stream: 'PANWATCH_NOTIFICATIONS', token_env: 'MY_NATS_TOKEN' } })
  await user.click(within(section).getByRole('button', { name: labels.notifications.add }))
  dialog = screen.getByRole('dialog')
  expect((within(dialog).getByPlaceholderText(labels.channels.channelNamePlaceholder) as HTMLInputElement).value).toBe('')
  expect(within(dialog).queryByPlaceholderText('PANWATCH_NATS_TOKEN')).toBeNull()
  await user.click(within(dialog).getByRole('button', { name: labels.dialogs.cancel }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(vi.mocked(fetchAPI).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})
