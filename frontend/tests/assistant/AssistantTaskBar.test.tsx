import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AssistantTaskSnapshot } from '@panwatch/api'
import { AssistantTaskBar } from '@/components/assistant/AssistantTaskBar'

function props(code = 'run_timeout') {
  const snapshot: AssistantTaskSnapshot = { id: 42, conversation_id: 1, status: 'failed', pending_approvals: [], error_code: code, can_retry: true }
  return { snapshot, error: null, disconnected: false, control: null, controlError: false, onStop: vi.fn(), onRetry: vi.fn(), onReconnect: vi.fn(), onConfigure: vi.fn(), onPermissions: vi.fn(), onContext: vi.fn(), onRevise: vi.fn(), onReview: vi.fn() }
}

describe('task recovery actions', () => {
  it('offers retry for a timeout before tools start', async () => {
    const p = props()
    render(<AssistantTaskBar {...p} />)
    await userEvent.click(screen.getByRole('button', { name: '重试任务' }))
    expect(p.onRetry).toHaveBeenCalledOnce()
  })

  it('routes context overflow to context compression and question revision', async () => {
    const p = props('ai_context_limit_exceeded')
    render(<AssistantTaskBar {...p} />)
    await userEvent.click(screen.getByRole('button', { name: '压缩上下文' }))
    await userEvent.click(screen.getByRole('button', { name: '修改问题' }))
    expect(p.onContext).toHaveBeenCalledOnce()
    expect(p.onRevise).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull()
  })

  it('routes a denied operation to tool permissions', async () => {
    const p = props('permission_denied')
    render(<AssistantTaskBar {...p} />)
    await userEvent.click(screen.getByRole('button', { name: '检查工具权限' }))
    expect(p.onPermissions).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull()
  })

  it('keeps unknown provider details out of the failure message', () => {
    render(<AssistantTaskBar {...props('new_provider_code')} error={{ code: 'new_provider_code', message: 'raw private provider details', retryable: false }} />)
    expect(screen.getByText('助手请求失败，请稍后重试。')).toBeTruthy()
    expect(screen.queryByText('raw private provider details')).toBeNull()
    expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull()
  })

  it('offers status checking after a control request fails', async () => {
    const p = props()
    p.snapshot.status = 'running'
    p.snapshot.error_code = null
    render(<AssistantTaskBar {...p} controlError />)
    await userEvent.click(screen.getByRole('button', { name: '重新连接' }))
    expect(p.onReconnect).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '停止任务' })).toBeTruthy()
  })

  it('lets a running task continue in the background without stopping it', async () => {
    const p = props()
    p.snapshot.status = 'running'
    p.snapshot.error_code = null
    const onBackground = vi.fn()
    render(<AssistantTaskBar {...p} onBackground={onBackground} />)
    await userEvent.click(screen.getByRole('button', { name: '后台运行' }))
    expect(onBackground).toHaveBeenCalledOnce()
    expect(p.onStop).not.toHaveBeenCalled()
  })
})
