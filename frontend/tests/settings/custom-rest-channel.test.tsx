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

const locales = [['en-US', en], ['zh-CN', zh]] as const

async function openCustomRestDialog(language: string, resource: typeof en | typeof zh) {
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
  const dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('combobox', { name: labels.dialogs.channelType }))
  await user.click(screen.getByRole('option', { name: labels.channels.types.customRest }))
  return { user, labels, section, dialog }
}

it.each(locales)('creates a Custom REST API channel with environment references in %s', async (language, resource) => {
  const { user, labels, section, dialog } = await openCustomRestDialog(language, resource)
  expect(within(dialog).getByText(labels.channels.customRestHint)).toBeTruthy()
  expect(within(dialog).getAllByRole('textbox')).toHaveLength(4)
  await user.type(within(dialog).getByLabelText(labels.dialogs.channelName), 'Gateway')
  await user.type(within(dialog).getByLabelText(labels.channels.fields.restEndpoint), 'https://gateway.example.net/notify')
  await user.type(within(dialog).getByPlaceholderText(labels.channels.placeholders.restTokenEnv), 'MY_REST_TOKEN')
  await user.type(within(dialog).getByPlaceholderText(labels.channels.placeholders.restCaEnv), 'MY_REST_CA_PEM')
  await user.type(within(dialog).getByRole('spinbutton'), '3.5')
  await user.click(within(dialog).getByRole('button', { name: labels.dialogs.create }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const request = vi.mocked(fetchAPI).mock.calls.find(([path, options]) => path === '/channels' && options?.method === 'POST')
  expect(request).toBeTruthy()
  expect(JSON.parse(String(request![1]!.body))).toEqual({
    name: 'Gateway', type: 'custom_rest', config: {
      endpoint_url: 'https://gateway.example.net/notify',
      token_env: 'MY_REST_TOKEN', tls_ca_env: 'MY_REST_CA_PEM', timeout_seconds: '3.5',
    },
  })
  await user.click(within(section).getByRole('button', { name: labels.notifications.add }))
  const reopened = screen.getByRole('dialog')
  expect((within(reopened).getByLabelText(labels.dialogs.channelName) as HTMLInputElement).value).toBe('')
  expect(within(reopened).queryByPlaceholderText(labels.channels.placeholders.restTokenEnv)).toBeNull()
})

for (const dismiss of ['cancel', 'escape'] as const) {
  it.each(locales)(`discards Custom REST API values after ${dismiss} and reopen in %s`, async (language, resource) => {
    const { user, labels, section, dialog } = await openCustomRestDialog(language, resource)
    await user.type(within(dialog).getByLabelText(labels.dialogs.channelName), 'Discard me')
    await user.type(within(dialog).getByLabelText(labels.channels.fields.restEndpoint), 'https://discard.example.net/notify')
    await user.type(within(dialog).getByPlaceholderText(labels.channels.placeholders.restTokenEnv), 'DISCARD_TOKEN')
    await user.type(within(dialog).getByPlaceholderText(labels.channels.placeholders.restCaEnv), 'DISCARD_CA')
    await user.type(within(dialog).getByRole('spinbutton'), '7')
    if (dismiss === 'cancel') {
      await user.click(within(dialog).getByRole('button', { name: labels.dialogs.cancel }))
    } else {
      await user.keyboard('{Escape}')
    }
    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(within(section).getByRole('button', { name: labels.notifications.add }))
    const reopened = screen.getByRole('dialog')
    expect((within(reopened).getByLabelText(labels.dialogs.channelName) as HTMLInputElement).value).toBe('')
    await user.click(within(reopened).getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: labels.channels.types.customRest }))
    for (const field of within(reopened).getAllByRole('textbox')) {
      expect((field as HTMLInputElement).value).toBe('')
    }
    expect((within(reopened).getByRole('spinbutton') as HTMLInputElement).value).toBe('')
    expect(vi.mocked(fetchAPI).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(0)
  })
}

it.each(locales)('requires an endpoint and constrains optional timeout to 1–10 seconds in %s', async (language, resource) => {
  const { user, labels, dialog } = await openCustomRestDialog(language, resource)
  const create = within(dialog).getByRole('button', { name: labels.dialogs.create }) as HTMLButtonElement
  const timeout = within(dialog).getByRole('spinbutton') as HTMLInputElement
  await user.type(within(dialog).getByLabelText(labels.dialogs.channelName), 'Local gateway')
  expect(create.disabled).toBe(true)
  await user.type(within(dialog).getByLabelText(labels.channels.fields.restEndpoint), 'http://127.0.0.1:8080/notify')
  expect(create.disabled).toBe(false)
  expect(timeout.min).toBe('1')
  expect(timeout.max).toBe('10')
  for (const [value, disabled] of [['0', true], ['11', true], ['1', false], ['10', false]] as const) {
    await user.clear(timeout)
    await user.type(timeout, value)
    expect(create.disabled).toBe(disabled)
  }
  await user.clear(timeout)
  expect(create.disabled).toBe(false)
  await user.click(create)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const request = vi.mocked(fetchAPI).mock.calls.find(([path, options]) => path === '/channels' && options?.method === 'POST')
  expect(JSON.parse(String(request![1]!.body))).toEqual({
    name: 'Local gateway', type: 'custom_rest',
    config: { endpoint_url: 'http://127.0.0.1:8080/notify', timeout_seconds: '' },
  })
})
