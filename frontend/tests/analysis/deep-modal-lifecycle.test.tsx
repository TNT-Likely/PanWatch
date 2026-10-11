import { StrictMode, useState } from 'react'
import i18n from '@/i18n'
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { tradingAgentsApi, subscribeSSE } from '@panwatch/api'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'
vi.mock('@panwatch/api',()=>({tradingAgentsApi:{findRunning:vi.fn(),getLatestForStock:vi.fn(),getProgress:vi.fn(),trigger:vi.fn()},subscribeSSE:vi.fn()}))
vi.mock('@panwatch/base-ui/components/ui/toast',()=>({useToast:()=>({toast:vi.fn()})}))
const props={open:true,onOpenChange:vi.fn(),stockId:1,stockName:'TELUS',stockSymbol:'T',stockMarket:'TSX'}
const running={trace_id:'tsx-trace',status:'running',stages:[],completed_stages:[],elapsed_sec:0,total_cost_usd:0} as any
const stop = vi.fn()
beforeEach(async()=>{vi.resetAllMocks();await i18n.changeLanguage('en-US');vi.mocked(subscribeSSE).mockReturnValue(stop);vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValue(null);vi.mocked(tradingAgentsApi.getProgress).mockResolvedValue(running);vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({trace_id:null,status:'none'})})
afterEach(()=>{cleanup();localStorage.clear();vi.useRealTimers()})
it('unsubscribes active TSX progress when Stocks closes and unmounts deep modal',async()=>{
 const stop=vi.fn();vi.mocked(subscribeSSE).mockReturnValue(stop);vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({trace_id:'tsx-trace',status:'running'})
 const {unmount}=render(<DeepAnalysisModal {...props}/>);await waitFor(()=>expect(subscribeSSE).toHaveBeenCalledTimes(1));unmount();expect(stop).toHaveBeenCalledTimes(1)
})
it('does not start progress listeners when trigger resolves after modal unmount',async()=>{
 let resolve!:(value:any)=>void;vi.mocked(tradingAgentsApi.trigger).mockReturnValue(new Promise(done=>{resolve=done}))
 const stop=vi.fn();vi.mocked(subscribeSSE).mockReturnValue(stop)
 const {unmount}=render(<DeepAnalysisModal {...props}/>);await waitFor(()=>expect(tradingAgentsApi.findRunning).toHaveBeenCalledWith('TSX:T'))
 fireEvent.click(screen.getByRole('button',{name:'Start analysis'}));await waitFor(()=>expect(tradingAgentsApi.trigger).toHaveBeenCalledTimes(1));unmount()
 await act(async()=>resolve({ok:true,trace_id:'tsx-trace'}));expect(subscribeSSE).not.toHaveBeenCalled()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function report(text: string) {
  return { agent_name: 'tradingagents', title: text, content: text, analysis_date: '2026-10-09', raw_data: { final_decision: text, suggestion: { action: 'hold', action_label: 'Hold', confidence: 7 } } } as any
}
function callbacks() { return vi.mocked(subscribeSSE).mock.calls.at(-1)![1] }
async function startRestoredRun() {
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'tsx-trace', status: 'running' })
  const view = render(<DeepAnalysisModal {...props} />)
  await waitFor(() => expect(subscribeSSE).toHaveBeenCalledTimes(1))
  return view
}

it.each(['resolve', 'reject'] as const)('ignores trigger %s after close and same-symbol reopen', async settle => {
  const pending = deferred<any>()
  vi.mocked(tradingAgentsApi.trigger).mockReturnValueOnce(pending.promise)
  const view = render(<DeepAnalysisModal {...props} />)
  await act(async () => {})
  fireEvent.click(screen.getByRole('button', { name: 'Start analysis' }))
  view.rerender(<DeepAnalysisModal {...props} open={false} />)
  view.rerender(<DeepAnalysisModal {...props} />)
  await act(async () => {})
  await act(async () => settle === 'resolve' ? pending.resolve({ trace_id: 'old-trigger' }) : pending.reject(new Error('Old trigger failure')))
  expect(subscribeSSE).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Start analysis' })).toBeTruthy()
  expect(screen.queryByText('Old trigger failure')).toBeNull()
  expect(localStorage.getItem('panwatch:tradingagents:running:TSX:T')).toBeNull()
})

it('a pending initial lookup cannot replace an explicitly started run', async () => {
  const restore = deferred<any>()
  vi.mocked(tradingAgentsApi.getLatestForStock).mockReturnValueOnce(restore.promise)
  vi.mocked(tradingAgentsApi.trigger).mockResolvedValue({ trace_id: 'new-run' } as any)
  render(<DeepAnalysisModal {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'Start analysis' }))
  await waitFor(() => expect(subscribeSSE).toHaveBeenCalledTimes(1))
  await act(async () => restore.resolve(report('Obsolete cached report')))
  expect(screen.queryByText('Obsolete cached report')).toBeNull()
  expect(screen.getByText('Analysis in progress...')).toBeTruthy()
})

it.each(['resolve', 'reject'] as const)('ignores late terminal-result %s after switching listing', async settle => {
  const latest = deferred<any>()
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValueOnce(null).mockReturnValueOnce(latest.promise).mockResolvedValue(report('Venture result'))
  const view = await startRestoredRun()
  const oldCallbacks = callbacks()
  act(() => oldCallbacks.onEvent?.({ event: 'progress', data: { ...running, status: 'success' } } as any))
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: null, status: 'none' })
  view.rerender(<DeepAnalysisModal {...props} stockMarket="TSXV" />)
  await screen.findByText('Venture result')
  await act(async () => settle === 'resolve' ? latest.resolve(report('Obsolete TSX result')) : latest.reject(new Error('Obsolete TSX failure')))
  expect(screen.getByText('Venture result')).toBeTruthy()
  expect(screen.queryByText(/Obsolete TSX/)).toBeNull()
})

it('stops fallback polling on unmount and rejects late transport callbacks', async () => {
  vi.useFakeTimers()
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'tsx-trace', status: 'running' })
  const view = render(<DeepAnalysisModal {...props} />)
  await act(async () => {})
  const oldCallbacks = callbacks()
  act(() => oldCallbacks.onFailed?.(new Error('SSE unavailable')))
  await act(async () => vi.advanceTimersByTimeAsync(4_000))
  expect(vi.mocked(tradingAgentsApi.getProgress).mock.calls.length).toBeGreaterThan(1)
  view.unmount()
  const calls = vi.mocked(tradingAgentsApi.getProgress).mock.calls.length
  await act(async () => { oldCallbacks.onClosed?.(); oldCallbacks.onFailed?.(new Error('Late close')); await vi.advanceTimersByTimeAsync(10_000) })
  expect(tradingAgentsApi.getProgress).toHaveBeenCalledTimes(calls)
  expect(stop).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('does not let pending polling regress a terminal SSE result or restart after close', async () => {
  const poll = deferred<any>()
  vi.mocked(tradingAgentsApi.getProgress).mockReturnValueOnce(poll.promise)
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValueOnce(null).mockResolvedValue(report('Completed Canadian report'))
  await startRestoredRun()
  const cb = callbacks()
  await act(async () => cb.onEvent?.({ event: 'progress', data: { ...running, status: 'success' } } as any))
  await screen.findByText('Completed Canadian report')
  await act(async () => { poll.resolve({ ...running, status: 'failed', run: { error: 'Old poll failure' } }); cb.onClosed?.(); cb.onFailed?.(new Error('Late failure')) })
  expect(screen.queryByText('Old poll failure')).toBeNull()
  expect(screen.getByText('Completed Canadian report')).toBeTruthy()
  expect(tradingAgentsApi.getProgress).toHaveBeenCalledTimes(1)
})

it.each(['escape', 'close'])('cleans the watcher when dismissed through the outer dialog %s', async method => {
  function Parent() {
    const [open, setOpen] = useState(true)
    return open ? <DeepAnalysisModal {...props} onOpenChange={setOpen} /> : null
  }
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'tsx-trace', status: 'running' })
  render(<Parent />)
  await waitFor(() => expect(subscribeSSE).toHaveBeenCalledTimes(1))
  if (method === 'escape') fireEvent.keyDown(document, { key: 'Escape' })
  else fireEvent.click(screen.getByRole('button', { name: '' }))
  expect(stop).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('survives StrictMode restoration and closes only its active subscription', async () => {
  const old = deferred<any>()
  vi.mocked(tradingAgentsApi.findRunning).mockReturnValueOnce(old.promise).mockResolvedValue({ trace_id: 'current-run', status: 'running' })
  const view = render(<StrictMode><DeepAnalysisModal {...props} /></StrictMode>)
  await waitFor(() => expect(subscribeSSE).toHaveBeenCalledTimes(1))
  await act(async () => old.resolve({ trace_id: 'obsolete-run', status: 'running' }))
  expect(subscribeSSE).toHaveBeenCalledTimes(1)
  expect(vi.mocked(subscribeSSE).mock.calls[0][0]).toContain('current-run')
  view.unmount()
  expect(stop).toHaveBeenCalledTimes(1)
})

it('retries a failed final snapshot after a terminal done hint closes the stream', async () => {
  vi.useFakeTimers()
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'tsx-trace', status: 'running' })
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValueOnce(null).mockResolvedValue(report('Recovered terminal report'))
  vi.mocked(tradingAgentsApi.getProgress).mockResolvedValueOnce(running).mockRejectedValueOnce(new Error('Temporary final snapshot failure')).mockResolvedValue({ ...running, status: 'success' })
  render(<DeepAnalysisModal {...props} />)
  await act(async () => {})
  const cb = callbacks()
  await act(async () => cb.onEvent?.({ event: 'done', data: { status: 'success' } } as any))
  await act(async () => { cb.onClosed?.(); await vi.advanceTimersByTimeAsync(2_000) })
  expect(screen.getByText('Recovered terminal report')).toBeTruthy()
  expect(stop).toHaveBeenCalledTimes(1)
})

it('coalesces slow polling instead of starving successful responses behind newer timer ticks', async () => {
  vi.useFakeTimers()
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'tsx-trace', status: 'running' })
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValueOnce(null).mockResolvedValue(report('Slow Canadian report'))
  vi.mocked(tradingAgentsApi.getProgress).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve({ ...running, status: 'success' }), 3_000)))
  render(<DeepAnalysisModal {...props} />)
  await act(async () => {})
  act(() => callbacks().onFailed?.(new Error('Use polling')))
  await act(async () => vi.advanceTimersByTimeAsync(15_000))
  expect(screen.getByText('Slow Canadian report')).toBeTruthy()
  expect(tradingAgentsApi.getProgress).toHaveBeenCalledTimes(1)
  expect(stop).toHaveBeenCalledTimes(1)
})
