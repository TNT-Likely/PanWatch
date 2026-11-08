import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import NotificationSourceDialog from '@/components/notifications/NotificationSourceDialog'

const target = {
  kind: 'agent_run' as const, id: 1, agent_name: 'premarket_outlook', status: 'success',
  result: 'Draft report', error: '', occurred_at: '2026-11-06T08:00:00Z',
  notify_attempted: false, notify_sent: false,
}

const locales = [
  { locale: 'zh-CN', incomplete: '运行已结束 · 研究未完成', validation: '结论校验未通过', review: '研究完成 · 待人工复核', complete: '研究完成', ended: '运行已结束', failed: '运行失败', skipped: '无实质变化，已跳过' },
  { locale: 'en-US', incomplete: 'Run ended · Research incomplete', validation: 'Validation failed', review: 'Research complete · Human review required', complete: 'Complete', ended: 'Run ended', failed: 'Run failed', skipped: 'No material change; skipped' },
]

describe.each(locales)('research run presentation ($locale)', text => {
  it('does not present a successful lifecycle as completed research after validation failure', async () => {
    await i18n.changeLanguage(text.locale)
    render(<NotificationSourceDialog target={{ ...target, completion: 'validation_failed', result_status: 'review', research_reason: 'Invalid manager report' }} onClose={vi.fn()} />)

    expect(screen.getByText(text.incomplete)).toBeTruthy()
    expect(screen.getByText(text.validation)).toBeTruthy()
    expect(screen.getByText('Invalid manager report')).toBeTruthy()
    expect(screen.queryByText(text.review)).toBeNull()
    expect(screen.queryByText(text.complete, { exact: true })).toBeNull()
  })

  it('keeps valid complete research separate from human review requirements', async () => {
    await i18n.changeLanguage(text.locale)
    render(<NotificationSourceDialog target={{ ...target, completion: 'complete', result_status: 'review' }} onClose={vi.fn()} />)

    expect(screen.getByText(text.review)).toBeTruthy()
    expect(screen.queryByText(text.incomplete)).toBeNull()
    expect(screen.queryByText(text.validation)).toBeNull()
  })

  it('shows validated ready research as complete', async () => {
    await i18n.changeLanguage(text.locale)
    render(<NotificationSourceDialog target={{ ...target, completion: 'complete', result_status: 'ready' }} onClose={vi.fn()} />)
    expect(screen.getByText(text.complete)).toBeTruthy()
    expect(screen.queryByText(text.incomplete)).toBeNull()
  })

  it.each([undefined, null])('does not infer research completion for legacy records (%s)', async value => {
    await i18n.changeLanguage(text.locale)
    render(<NotificationSourceDialog target={{ ...target, completion: value, result_status: value, research_reason: value }} onClose={vi.fn()} />)
    expect(screen.getByText(text.ended)).toBeTruthy()
    expect(screen.queryByText(text.complete, { exact: true })).toBeNull()
  })

  it('uses a review reason when older results omit completion', async () => {
    await i18n.changeLanguage(text.locale)
    render(<NotificationSourceDialog target={{ ...target, result_status: 'review', research_reason: 'Research deadline exhausted' }} onClose={vi.fn()} />)
    expect(screen.getByText(text.incomplete)).toBeTruthy()
    expect(screen.getByText('Research deadline exhausted')).toBeTruthy()
  })

  it('keeps execution failures and skipped research distinct', async () => {
    await i18n.changeLanguage(text.locale)
    const view = render(<NotificationSourceDialog target={{ ...target, status: 'failed', error: 'Provider unavailable' }} onClose={vi.fn()} />)
    expect(screen.getByText(text.failed)).toBeTruthy()
    expect(screen.getByText('Provider unavailable')).toBeTruthy()
    view.rerender(<NotificationSourceDialog target={{ ...target, completion: 'skipped', result_status: 'skipped' }} onClose={vi.fn()} />)
    expect(screen.getByText(text.skipped)).toBeTruthy()
    expect(screen.queryByText(text.incomplete)).toBeNull()
  })
})
