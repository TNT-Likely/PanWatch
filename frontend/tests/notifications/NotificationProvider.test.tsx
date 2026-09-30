import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { chatApi, notificationsApi, type NotificationItem } from '@panwatch/api'
import { NotificationBell, NotificationProvider } from '@/components/notifications/NotificationProvider'

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast }) }))
vi.mock('@panwatch/api', () => ({
  chatApi: { getActiveAssistantTasks: vi.fn(), readAssistantNotifications: vi.fn() },
  notificationsApi: { summary: vi.fn(), list: vi.fn(), read: vi.fn(), archive: vi.fn(), target: vi.fn() },
}))
const summary = { unread_count: 0, pending_action_count: 0, observed_id: 0 }
const notice = (id: number, source: NotificationItem['source'] = 'assistant'): NotificationItem => ({ id, source, event_type: source === 'assistant' ? 'assistant_completed' : source === 'agent' ? 'agent_completed' : 'price_alert_hit', severity: 'info', attention: 'informational', title: '后台研究', template_key: source === 'assistant' ? 'assistant_completed' : source === 'agent' ? 'agent_completed' : 'price_alert_hit', template_params: {}, group_key: '', toast_eligible: true, occurred_at: '2026-09-30T00:00:00Z', read_at: null, resolved_at: null, expires_at: null, archived_at: null, action_required: false, available: true, actions: source === 'assistant' ? [{ kind: 'assistant_conversation', conversation_id: 1 }] : [{ kind: 'agent_run', run_id: 1 }] })
function Page({ mobile = false }: { mobile?: boolean }) { const location = useLocation(); return <><NotificationBell mobile={mobile} /><p data-testid="path">{location.pathname}</p></> }
function show(path = '/portfolio', mobile = false) { return render(<MemoryRouter initialEntries={[path]}><NotificationProvider><Page mobile={mobile} /></NotificationProvider></MemoryRouter>) }
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  vi.mocked(chatApi.getActiveAssistantTasks).mockResolvedValue([])
  vi.mocked(notificationsApi.summary).mockResolvedValue(summary)
  vi.mocked(notificationsApi.list).mockResolvedValue({ items: [], next_cursor: null, observed_id: 0 })
  vi.mocked(notificationsApi.read).mockResolvedValue({ updated: 1 })
  vi.mocked(notificationsApi.archive).mockResolvedValue({ updated: 1 })
  vi.mocked(notificationsApi.target).mockResolvedValue({ kind: 'assistant_conversation', conversation_id: 1 })
})

describe('global notifications', () => {
  it('opens on desktop hover, preserves focus, and stays open across the portal gap', async () => {
    show()
    const bell = await screen.findByTestId('notification-bell')
    const previousFocus = document.activeElement
    fireEvent.mouseEnter(bell)
    const preview = await screen.findByTestId('notification-preview')
    await screen.findByText('暂无新通知')
    expect(document.activeElement).toBe(previousFocus)
    fireEvent.mouseLeave(bell)
    fireEvent.mouseEnter(preview)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 220)) })
    expect(screen.getByTestId('notification-preview')).toBeTruthy()
    expect(notificationsApi.read).not.toHaveBeenCalled()
    fireEvent.mouseLeave(preview)
    await waitFor(() => expect(screen.queryByTestId('notification-preview')).toBeNull())
    expect(document.activeElement).toBe(previousFocus)
  })

  it('closes after leaving the desktop bell without entering the preview', async () => {
    show()
    const bell = await screen.findByTestId('notification-bell')
    fireEvent.mouseEnter(bell)
    await screen.findByTestId('notification-preview')
    fireEvent.mouseLeave(bell)
    await waitFor(() => expect(screen.queryByTestId('notification-preview')).toBeNull())
  })

  it('uses click instead of hover on mobile', async () => {
    show('/portfolio', true)
    const bell = await screen.findByTestId('notification-bell')
    fireEvent.mouseEnter(bell)
    expect(screen.queryByTestId('notification-preview')).toBeNull()
    fireEvent.click(bell)
    expect(await screen.findByText('暂无新通知')).toBeTruthy()
    fireEvent.mouseLeave(bell)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 220)) })
    expect(screen.getByTestId('notification-preview')).toBeTruthy()
  })

  it('retains keyboard access and closes a focused preview with Escape', async () => {
    show()
    const bell = await screen.findByTestId('notification-bell')
    bell.focus()
    // A native keyboard activation dispatches a click with no pointer detail.
    fireEvent.click(bell, { detail: 0 })
    await screen.findByText('暂无新通知')
    expect(screen.getByTestId('notification-preview').contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('notification-preview')).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(bell))
  })

  it('opens an empty lightweight preview without reading or displaying history', async () => {
    vi.mocked(notificationsApi.list).mockImplementation(async filter => ({ items: filter?.view === 'attention' ? [] : [{ ...notice(900), title: '已经读过的历史', read_at: '2026-09-30T00:00:00Z' }], observed_id: 900, next_cursor: null }))
    show(); fireEvent.click(await screen.findByRole('button', { name: '通知：0 条未读' }))
    expect(await screen.findByText('暂无新通知')).toBeTruthy()
    expect(screen.queryByText('已经读过的历史')).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByTestId('notification-panel')).toBeNull()
    expect(notificationsApi.read).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '查看全部通知' }))
    expect(await screen.findByText('已经读过的历史')).toBeTruthy()
  })

  it('keeps read approvals in the preview and marks only the observed attention scope read', async () => {
    const approval = { ...notice(910), title: '等待批准', event_type: 'assistant_awaiting_approval' as const, template_key: 'assistant_awaiting_approval', read_at: '2026-09-30T00:00:00Z', action_required: true }
    const report = { ...notice(911, 'agent'), title: '新的报告', toast_eligible: false }
    vi.mocked(notificationsApi.summary).mockResolvedValue({ unread_count: 1, pending_action_count: 1, observed_id: 911 })
    vi.mocked(notificationsApi.list).mockImplementation(async filter => ({ items: filter?.view === 'attention' ? [approval, report] : [report], next_cursor: null, observed_id: 911 }))
    show(); fireEvent.click(await screen.findByRole('button', { name: '通知：1 条未读' }))
    expect(await screen.findByText('等待批准')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '归档' })).toBeNull()
    expect(notificationsApi.read).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '全部已读' }))
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith({ through_id: 911, view: 'attention' }))
    expect(screen.getByText('等待批准')).toBeTruthy()
    expect(notificationsApi.target).not.toHaveBeenCalled()
  })
  it('shows no unread badge for running tasks and offers a separate progress entry', async () => {
    vi.mocked(chatApi.getActiveAssistantTasks).mockResolvedValue([{ id: 42, conversation_id: 1, title: '后台研究', status: 'running', current_step: 2 }])
    show()
    const entry = await screen.findByRole('button', { name: '助手任务：1 项进行中' })
    expect(entry.textContent).toContain('助手任务 · 1')
    expect(entry.querySelector('.animate-spin')).toBeNull()
    expect(screen.queryByTestId('notification-badge')).toBeNull()
    fireEvent.click(entry)
    expect(await screen.findByText('步骤 2', { exact: false })).toBeTruthy()
    expect(screen.queryByText('任务通知')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '查看进度: 后台研究' }))
    expect(screen.getByTestId('path').textContent).toBe('/assistant/1')
  })

  it.each(['/assistant', '/assistant/1'])('hides the redundant global task entry on %s', async path => {
    vi.mocked(chatApi.getActiveAssistantTasks).mockResolvedValue([{ id: 42, conversation_id: 1, title: '后台研究', status: 'running', current_step: 2 }])
    show(path)
    await waitFor(() => expect(chatApi.getActiveAssistantTasks).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByTestId('assistant-task-entry')).toBeNull()
    expect(screen.getByTestId('notification-bell')).toBeTruthy()
  })

  it('deduplicates an actionable toast and resolves its original conversation on the server', async () => {
    const item = notice(1001)
    vi.mocked(notificationsApi.summary).mockResolvedValue({ ...summary, unread_count: 1, observed_id: item.id })
    vi.mocked(notificationsApi.list).mockResolvedValue({ items: [item], next_cursor: null, observed_id: item.id })
    show()
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(toast).toHaveBeenCalledTimes(1)
    vi.mocked(notificationsApi.summary).mockResolvedValue(summary)
    await act(async () => { await toast.mock.calls[0][2].onClick() })
    await waitFor(() => expect(screen.getByTestId('path').textContent).toBe('/assistant/1'))
    expect(notificationsApi.target).toHaveBeenCalledWith(1001)
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith({ ids: [1001] }))
  })

  it('marks visible approval read but does not approve or execute anything', async () => {
    const item = { ...notice(1002), event_type: 'assistant_awaiting_approval' as const, action_required: true }
    vi.mocked(notificationsApi.summary).mockResolvedValueOnce({ unread_count: 1, pending_action_count: 1, observed_id: item.id }).mockResolvedValue({ unread_count: 0, pending_action_count: 1, observed_id: item.id })
    vi.mocked(notificationsApi.list).mockResolvedValue({ items: [item], next_cursor: null, observed_id: item.id })
    show('/assistant/1')
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith({ ids: [1002] }))
    expect(toast).not.toHaveBeenCalled()
    expect(notificationsApi.target).not.toHaveBeenCalled()
  })

  it('suppresses upgrade toasts and waits for foreground before presenting new notices', async () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    const items = [{ ...notice(1003), toast_eligible: false }, notice(1004)]
    vi.mocked(notificationsApi.summary).mockResolvedValue({ ...summary, unread_count: 2, observed_id: 1004 })
    vi.mocked(notificationsApi.list).mockResolvedValue({ items, next_cursor: null, observed_id: 1004 })
    show(); await screen.findByRole('button', { name: '通知：2 条未读' })
    expect(toast).not.toHaveBeenCalled()
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
  })

  it('shares three sources, filters/paginates, retains read approvals and disables their archive button', async () => {
    const approval = { ...notice(1010), event_type: 'assistant_awaiting_approval' as const, template_key: 'assistant_awaiting_approval', read_at: '2026-09-30T01:00:00Z', action_required: true }
    const agent = { ...notice(1011, 'agent'), title: '日报', toast_eligible: false }
    const market = { ...notice(1012, 'market'), title: '价格规则', toast_eligible: false }
    vi.mocked(notificationsApi.list).mockImplementation(async (filter = {}) => ({ items: filter.cursor ? [market] : filter.source === 'agent' ? [agent] : filter.view === 'pending' ? [approval] : [approval, agent], next_cursor: !filter.cursor && !filter.source && filter.view === 'all' ? 'next' : null, observed_id: 1012 }))
    show(); fireEvent.click(await screen.findByRole('button', { name: '通知：0 条未读' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看全部通知' }))
    await screen.findByText('日报')
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
    expect(await screen.findByText('价格规则')).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox', { name: '通知来源' }), { target: { value: 'agent' } })
    await waitFor(() => expect(notificationsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'agent' }), expect.any(AbortSignal)))
    fireEvent.click(screen.getByRole('button', { name: '当前筛选全部已读' }))
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith({ source: 'agent', view: 'all', through_id: 1012 }))
    fireEvent.change(screen.getByRole('combobox', { name: '通知来源' }), { target: { value: '' } })
    fireEvent.change(screen.getByRole('combobox', { name: '通知视图' }), { target: { value: 'pending' } })
    await screen.findByText('有操作等待审批；已读不会执行操作。')
    expect((screen.getByRole('button', { name: '归档' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('opens an Agent run with its independent external delivery state', async () => {
    const item = { ...notice(1020, 'agent'), toast_eligible: false }
    vi.mocked(notificationsApi.list).mockResolvedValue({ items: [item], next_cursor: null, observed_id: item.id })
    vi.mocked(notificationsApi.target).mockResolvedValue({ kind: 'agent_run', id: 1, agent_name: 'daily_report', status: 'success', result: '**Report result**', error: '', occurred_at: item.occurred_at, notify_attempted: true, notify_sent: false })
    show(); fireEvent.click(await screen.findByRole('button', { name: '通知：0 条未读' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看全部通知' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看报告' }))
    expect(await screen.findByText('Report result')).toBeTruthy()
    expect(screen.getByText('运行已完成')).toBeTruthy()
    expect(screen.getByText('外部渠道未送达、未配置或已跳过')).toBeTruthy()
  })
})

describe('notification failure groups', () => {
  it('folds consecutive failures and reads all loaded records in the collapsed group', async () => {
    const first = { ...notice(1040, 'agent'), event_type: 'agent_failed' as const, template_key: 'agent_failed', group_key: 'failure-series', title: '重复失败样例', toast_eligible: false }
    const second = { ...first, id: 1039 }
    vi.mocked(notificationsApi.list).mockResolvedValue({ items: [first, second], next_cursor: null, observed_id: first.id })
    show(); fireEvent.click(await screen.findByRole('button', { name: '通知：0 条未读' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看全部通知' }))
    expect(await screen.findByRole('button', { name: '展开已加载的 2 条连续失败记录' })).toBeTruthy()
    expect(screen.getAllByText('重复失败样例')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '设为已读' }))
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith({ ids: [1040, 1039], source: undefined, view: 'all' }))
  })
})
