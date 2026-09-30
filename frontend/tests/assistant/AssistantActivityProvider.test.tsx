import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { chatApi, type AssistantActivity } from '@panwatch/api'
import { AssistantActivityBell, AssistantActivityProvider } from '@/components/assistant/AssistantActivityProvider'

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast }) }))
vi.mock('@panwatch/api', () => ({ chatApi: { getAssistantActivity: vi.fn(), readAssistantNotifications: vi.fn() } }))
const empty: AssistantActivity = { active_tasks: [], notifications: [], unread_count: 0, notification_cursor: 0 }
const notice = { id: 8, task_id: 42, conversation_id: 1, title: '后台研究', kind: 'completed' as const, read_at: null }
function Page() {
  const location = useLocation()
  return <><AssistantActivityBell /><p data-testid="path">{location.pathname}</p></>
}
function renderPage(path = '/') {
  return render(<MemoryRouter initialEntries={[path]}><AssistantActivityProvider><Page /></AssistantActivityProvider></MemoryRouter>)
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(empty)
  vi.mocked(chatApi.readAssistantNotifications).mockResolvedValue({ updated: 1 })
})

describe('global assistant activity', () => {
  it('presents an actionable completion toast once and returns to the original conversation', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...empty, notifications: [notice], unread_count: 1, notification_cursor: 8 })
    renderPage()
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
    expect(toast.mock.calls[0][0]).toBe('“后台研究”已完成')
    expect(toast.mock.calls[0][1]).toBe('success')
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(toast).toHaveBeenCalledTimes(1)
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...empty, notifications: [{ ...notice, read_at: '2026-09-30T00:00:00Z' }] })
    await act(async () => toast.mock.calls[0][2].onClick())
    expect(screen.getByTestId('path').textContent).toBe('/assistant/1')
    expect(chatApi.readAssistantNotifications).toHaveBeenCalledWith({ ids: [8] })
  })

  it('marks the currently visible conversation outcome read without interrupting it with a toast', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValueOnce({ ...empty, notifications: [notice], unread_count: 1, notification_cursor: 8 }).mockResolvedValue(empty)
    renderPage('/assistant/1')
    await waitFor(() => expect(chatApi.readAssistantNotifications).toHaveBeenCalledWith({ ids: [8] }))
    expect(toast).not.toHaveBeenCalled()
  })

  it('waits for the user to return before presenting hidden-tab notifications', async () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...empty, notifications: [{ ...notice, kind: 'awaiting_approval' }], unread_count: 1, notification_cursor: 8 })
    renderPage()
    await screen.findByRole('button', { name: /1 条未读/ })
    expect(toast).not.toHaveBeenCalled()
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
    expect(toast.mock.calls[0][0]).toBe('“后台研究”等待审批')
    expect(toast.mock.calls[0][1]).toBe('info')
  })

  it('lists a running task on another page and opens its progress', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...empty, active_tasks: [{ id: 42, conversation_id: 1, title: '后台研究', status: 'running', current_step: 2 }] })
    const user = userEvent.setup()
    renderPage('/portfolio')
    await user.click(await screen.findByRole('button', { name: /1 项进行中/ }))
    expect(await screen.findByText('步骤 2', { exact: false })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '查看进度: 后台研究' }))
    expect(screen.getByTestId('path').textContent).toBe('/assistant/1')
    expect(screen.queryByTestId('assistant-activity-panel')).toBeNull()
  })

  it('aggregates multiple outcomes and keeps unread notifications until explicit viewing', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...empty, notifications: [notice, { ...notice, id: 9, conversation_id: 2, kind: 'failed' }], unread_count: 2, notification_cursor: 9 })
    renderPage()
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
    expect(toast.mock.calls[0][0]).toBe('有 2 条新的助手任务通知')
    act(() => toast.mock.calls[0][2].onClick())
    expect(await screen.findByText('任务执行失败，请打开会话查看原因和恢复方式。')).toBeTruthy()
    expect(chatApi.readAssistantNotifications).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '全部已读' }))
    await waitFor(() => expect(chatApi.readAssistantNotifications).toHaveBeenCalledWith({ through_id: 9 }))
  })
})
