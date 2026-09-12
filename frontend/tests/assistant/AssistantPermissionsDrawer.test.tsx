import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const { getAgentPermissions, updateAgentPermission, getAssistantConfig } = vi.hoisted(() => ({
  getAgentPermissions: vi.fn(),
  updateAgentPermission: vi.fn(),
  getAssistantConfig: vi.fn(),
}))

vi.mock('@panwatch/api', () => ({
  chatApi: { getAgentPermissions, updateAgentPermission, getAssistantConfig },
}))

import { AssistantPermissionsDrawer } from '@/components/assistant/AssistantPermissionsDrawer'

describe('AssistantPermissionsDrawer', () => {
  it('loads tool permissions and saves a per-tool override from the assistant', async () => {
    const permissions = {
      defaults: [
        { risk: 'read' as const, mode: 'allow' as const },
        { risk: 'write' as const, mode: 'ask' as const },
        { risk: 'external' as const, mode: 'ask' as const },
        { risk: 'destructive' as const, mode: 'deny' as const },
      ],
      tools: [
        { name: 'get_portfolio', title: '查询持仓', risk: 'read' as const, mode: 'allow' as const, confirmation_required: false },
      ],
    }
    getAgentPermissions.mockResolvedValue(permissions)
    getAssistantConfig.mockResolvedValue({
      compression_model_id: null,
      compression_temperature: 0.1,
      summary_max_tokens: 800,
      max_tokens: 12000,
      soft_limit_tokens: 8400,
      hard_limit_tokens: 10200,
      keep_recent_messages: 8,
      models: [],
    })
    updateAgentPermission.mockResolvedValue({
      ...permissions,
      tools: [{ ...permissions.tools[0], mode: 'ask' as const }],
    })

    render(<AssistantPermissionsDrawer open onOpenChange={vi.fn()} />)

    await screen.findByLabelText('查询持仓')
    fireEvent.change(screen.getByLabelText('查询持仓'), { target: { value: 'ask' } })

    await waitFor(() => expect(updateAgentPermission).toHaveBeenCalledWith({
      selector_kind: 'tool',
      selector_value: 'get_portfolio',
      mode: 'ask',
      risk: 'read',
    }))
  })
})
