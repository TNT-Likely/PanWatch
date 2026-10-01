import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { chatApi, type AssistantContextExportJob } from '@panwatch/api'
import { AssistantContextExportDialog } from '@/components/assistant/AssistantContextExportDialog'
import { AssistantExportHistoryDialog } from '@/components/assistant/AssistantExportHistoryDialog'

vi.mock('@panwatch/api', () => ({ chatApi: {
  exportConversationContext: vi.fn(), getContextExport: vi.fn(), retryContextExport: vi.fn(), listContextExports: vi.fn(),
} }))

function job(overrides: Partial<AssistantContextExportJob> = {}): AssistantContextExportJob {
  return {
    id: 7, conversation_id: 8, title: '持仓风险', language: 'zh-CN', created_at: '2026-10-01T00:00:00Z',
    started_at: null, finished_at: null, message_count: 2, status: 'queued',
    processed_chars: 0, total_chars: 100, completed_parts: 0, error_code: null, result: null, ...overrides,
  }
}

const result = { content: '# 会话上下文总结\n\n## 下一步\n- 更新行情\n', filename: 'context.md',
  message_count: 2, last_message_id: 5, exported_at: '2026-10-01T00:00:00Z', incomplete: false }

async function tick(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

describe('assistant context exports', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks() })
  afterEach(() => { cleanup(); vi.useRealTimers() })

  it('submits once in StrictMode and stops local polling when closed', async () => {
    vi.mocked(chatApi.exportConversationContext).mockResolvedValue(job())
    const view = render(<StrictMode><AssistantContextExportDialog conversationId={8} onClose={vi.fn()} /></StrictMode>)
    await tick()
    expect(chatApi.exportConversationContext).toHaveBeenCalledTimes(1)
    expect(chatApi.exportConversationContext).toHaveBeenCalledWith(8, 'zh-CN', expect.any(AbortSignal))
    expect(screen.getByRole('status').textContent).toContain('排队')
    const signal = vi.mocked(chatApi.exportConversationContext).mock.calls[0][2]
    view.unmount(); await tick(3000)
    expect(signal?.aborted).toBe(true)
    expect(chatApi.getContextExport).not.toHaveBeenCalled()
  })

  it('polls the accepted job and renders the completed Markdown', async () => {
    vi.mocked(chatApi.exportConversationContext).mockResolvedValue(job())
    vi.mocked(chatApi.getContextExport).mockResolvedValue(job({ status: 'completed', result }))
    render(<AssistantContextExportDialog conversationId={8} onClose={vi.fn()} />)
    await tick(); await tick(2000)
    expect(chatApi.getContextExport).toHaveBeenCalledWith(7, expect.any(AbortSignal))
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(result.content)
    await tick(6000)
    expect(chatApi.getContextExport).toHaveBeenCalledTimes(1)
  })

  it('opens an existing export without submitting and retries the failed job', async () => {
    vi.mocked(chatApi.getContextExport).mockResolvedValue(job({ status: 'failed', error_code: 'assistant_export_timeout' }))
    vi.mocked(chatApi.retryContextExport).mockResolvedValue(job({ status: 'completed', result }))
    render(<AssistantContextExportDialog conversationId={8} exportId={7} onClose={vi.fn()} />)
    await tick()
    expect(screen.getByRole('alert').textContent).toContain('超时')
    fireEvent.click(screen.getByRole('button', { name: /重试|重新生成/ })); await tick()
    expect(chatApi.retryContextExport).toHaveBeenCalledWith(7, expect.any(AbortSignal))
    expect(chatApi.exportConversationContext).not.toHaveBeenCalled()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(result.content)
  })

  it('paginates history and opens saved detail without creating another job', async () => {
    vi.mocked(chatApi.listContextExports).mockResolvedValueOnce({ items: [job({ status: 'completed' })], next_cursor: 7 })
      .mockResolvedValueOnce({ items: [job({ id: 6, status: 'failed', title: '旧导出' })], next_cursor: null })
    vi.mocked(chatApi.getContextExport).mockResolvedValue(job({ status: 'completed', result }))
    render(<AssistantExportHistoryDialog conversationId={8} onClose={vi.fn()} />)
    await tick()
    expect(chatApi.listContextExports).toHaveBeenCalledWith({ conversationId: 8, beforeId: undefined }, expect.any(AbortSignal))
    fireEvent.click(screen.getByRole('button', { name: /加载更多|更多记录/ })); await tick()
    expect(chatApi.listContextExports).toHaveBeenLastCalledWith({ conversationId: 8, beforeId: 7 }, expect.any(AbortSignal))
    expect(screen.getByText('旧导出')).toBeTruthy()
    fireEvent.click(screen.getByText('持仓风险')); await tick()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(result.content)
    expect(chatApi.exportConversationContext).not.toHaveBeenCalled()
  })

  it('polls only active history records and updates their finished state', async () => {
    vi.mocked(chatApi.listContextExports).mockResolvedValueOnce({ items: [job()], next_cursor: null })
      .mockResolvedValue({ items: [job({ status: 'completed' })], next_cursor: null })
    render(<AssistantExportHistoryDialog conversationId={8} onClose={vi.fn()} />)
    await tick(); await tick(3000)
    expect(chatApi.listContextExports).toHaveBeenLastCalledWith({ ids: [7], limit: 50 }, expect.any(AbortSignal))
    expect(screen.getByText(/已完成/)).toBeTruthy()
    await tick(6000)
    expect(chatApi.listContextExports).toHaveBeenCalledTimes(2)
  })
})
