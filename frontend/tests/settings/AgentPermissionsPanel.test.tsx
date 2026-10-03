import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentPermissionsPanel } from '@/components/assistant/AgentPermissionsPanel'

afterEach(cleanup)
describe('agent tool permission settings', () => {
  it('uses shared selectors, sends overrides and locks destructive permissions', async () => {
    const onChange = vi.fn()
    render(<AgentPermissionsPanel permissions={{
      defaults: [
        { risk: 'read', mode: 'allow' }, { risk: 'write', mode: 'ask' },
        { risk: 'external', mode: 'ask' }, { risk: 'destructive', mode: 'deny' },
      ], tools: [
        { name: 'create_alert', title: '创建提醒', risk: 'write', mode: 'ask', confirmation_required: false },
        { name: 'delete_alert', title: '删除提醒', risk: 'destructive', mode: 'deny', confirmation_required: false },
      ],
    }} onChange={onChange} />)
    expect(screen.getByRole('combobox', { name: '读取默认权限' }).textContent).toContain('允许')
    expect((screen.getByRole('combobox', { name: '破坏性操作默认权限' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('combobox', { name: '删除提醒' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(screen.getByRole('combobox', { name: '修改默认权限' }))
    await userEvent.click(await screen.findByRole('option', { name: '直接允许' }))
    expect(onChange).toHaveBeenCalledWith({ selector_kind: 'risk', selector_value: 'write', mode: 'allow', risk: 'write' })
    await userEvent.click(screen.getByRole('combobox', { name: '创建提醒' }))
    await userEvent.click(await screen.findByRole('option', { name: '直接允许' }))
    expect(onChange).toHaveBeenCalledWith({ selector_kind: 'tool', selector_value: 'create_alert', mode: 'allow', risk: 'write' })
  })
})
