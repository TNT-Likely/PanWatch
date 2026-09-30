import { useCallback, useEffect, useRef, useState } from 'react'
import {
  chatApi,
  type AssistantApproval,
  type AssistantStreamError,
  type AssistantTaskSnapshot,
  type AssistantTaskStatus,
  type ChatStreamCallbacks,
} from '@panwatch/api'
import { signalAssistantActivityChange } from '@/lib/assistant-activity'

export const taskStorageKey = (conversationId: number) => 'panwatch:assistant-task:' + conversationId
export const isActiveTask = (status: AssistantTaskStatus) => (
  !['completed', 'failed', 'cancelled', 'expired', 'dead_letter'].includes(status)
)

function approvalFromSnapshot(approval: AssistantTaskSnapshot['pending_approvals'][number]): AssistantApproval {
  return {
    id: approval.id,
    tool_title: approval.presentation?.tool_title || approval.tool_name,
    risk: approval.risk,
    summary: approval.presentation?.summary || approval.tool_name,
    expires_at: approval.expires_at,
    status: 'pending',
  }
}

interface TaskCallbacks extends ChatStreamCallbacks {
  onResetStream: () => void
  onReloadMessages: (conversationId: number) => Promise<unknown>
}

type StreamOperation = (callbacks: ChatStreamCallbacks, signal: AbortSignal) => Promise<void>

/** One lifecycle for initial sends, event replay, retries, and approval resumes. */
export function useAssistantTask(callbacks: TaskCallbacks) {
  const callbacksRef = useRef(callbacks)
  callbacksRef.current = callbacks
  const [snapshot, setSnapshot] = useState<AssistantTaskSnapshot | null>(null)
  const snapshotRef = useRef<AssistantTaskSnapshot | null>(null)
  const [error, setError] = useState<AssistantStreamError | null>(null)
  const [controlError, setControlError] = useState(false)
  const [disconnected, setDisconnected] = useState(false)
  const [sending, setSending] = useState(false)
  const [control, setControl] = useState<'stopping' | 'retrying' | 'checking' | null>(null)
  const controlRef = useRef(false)
  const controlSequenceRef = useRef(0)
  const [pendingApprovals, setPendingApprovals] = useState<AssistantApproval[]>([])
  const [decidingApprovalId, setDecidingApprovalId] = useState<string | null>(null)
  const decidingApprovalRef = useRef<string | null>(null)
  const conversationRef = useRef<number | null>(null)
  const epochRef = useRef(0)
  const streamRef = useRef<AbortController | null>(null)

  const updateSnapshot = useCallback((next: AssistantTaskSnapshot | null) => {
    const previous = snapshotRef.current
    snapshotRef.current = next
    setSnapshot(next)
    if (next && (previous?.id !== next.id || previous.status !== next.status)) signalAssistantActivityChange()
  }, [])

  const reset = useCallback((conversationId: number | null) => {
    if (conversationRef.current === conversationId) return
    conversationRef.current = conversationId
    epochRef.current += 1
    streamRef.current?.abort()
    streamRef.current = null
    updateSnapshot(null)
    setPendingApprovals([])
    setDecidingApprovalId(null)
    decidingApprovalRef.current = null
    setError(null)
    setControlError(false)
    setDisconnected(false)
    setSending(false)
    setControl(null)
    controlRef.current = false
    controlSequenceRef.current += 1
    callbacksRef.current.onResetStream()
  }, [updateSnapshot])

  const releaseControl = useCallback((sequence: number) => {
    if (controlSequenceRef.current !== sequence) return
    controlRef.current = false
    setControl(null)
  }, [])

  useEffect(() => () => {
    epochRef.current += 1
    streamRef.current?.abort()
  }, [])

  const applySnapshot = useCallback((next: AssistantTaskSnapshot) => {
    updateSnapshot(next)
    if (next.status !== 'completed') next.trace?.forEach((event) => callbacksRef.current.onTrace?.(event))
    if (next.status === 'awaiting_approval') {
      setPendingApprovals((previous) => [
        ...previous.filter((item) => item.status !== 'pending'),
        ...next.pending_approvals.map(approvalFromSnapshot),
      ])
    } else if (!isActiveTask(next.status)) {
      setPendingApprovals([])
    }
    if (next.status === 'failed' || next.status === 'expired' || next.status === 'dead_letter') {
      setError((previous) => ({
        code: next.error_code || previous?.code || 'assistant_unknown_error',
        message: previous?.message || '',
        retryable: previous?.retryable ?? true,
      }))
    } else {
      setError(null)
    }
    if (isActiveTask(next.status)) {
      sessionStorage.setItem(taskStorageKey(next.conversation_id), String(next.id))
    } else {
      sessionStorage.removeItem(taskStorageKey(next.conversation_id))
    }
  }, [updateSnapshot])

  const runStream = useCallback(async (conversationId: number, operation: StreamOperation, afterEventId = 0) => {
    streamRef.current?.abort()
    const controller = new AbortController()
    streamRef.current = controller
    const epoch = ++epochRef.current
    const isCurrent = () => epochRef.current === epoch && conversationRef.current === conversationId
    setSending(true)
    setDisconnected(false)
    setControlError(false)
    callbacksRef.current.onResetStream()
    let cursor = afterEventId
    let terminal = false
    let receivedAnswer = false
    let streamError: AssistantStreamError | null = null
    const remember = (id: number, status: AssistantTaskStatus) => {
      if (id <= 0) return
      const previous = snapshotRef.current
      updateSnapshot(previous?.id === id
        ? { ...previous, status }
        : { id, conversation_id: conversationId, status, pending_approvals: [], created_at: new Date().toISOString() })
      sessionStorage.setItem(taskStorageKey(conversationId), String(id))
    }
    const updateStatus = (status: AssistantTaskStatus) => {
      const previous = snapshotRef.current
      if (!previous) return
      updateSnapshot({
        ...previous,
        status,
        ...(status === 'running' && !previous.started_at ? { started_at: new Date().toISOString() } : {}),
        ...(!isActiveTask(status) ? { finished_at: new Date().toISOString() } : {}),
      })
    }
    const handlers: ChatStreamCallbacks = {
      onTaskCreated: (id) => { if (isCurrent() && !terminal) remember(id, 'queued') },
      onRunStarted: (info) => {
        if (!isCurrent() || terminal) return
        remember(info.taskId, 'running')
        updateStatus('running')
        callbacksRef.current.onRunStarted?.(info)
      },
      onContextPrepared: (info) => { if (isCurrent() && !terminal) callbacksRef.current.onContextPrepared?.(info) },
      onToken: (token) => { if (isCurrent() && !terminal) callbacksRef.current.onToken?.(token) },
      onToolCallStart: (info) => { if (isCurrent() && !terminal) callbacksRef.current.onToolCallStart?.(info) },
      onToolResult: (info) => { if (isCurrent() && !terminal) callbacksRef.current.onToolResult?.(info) },
      onPlan: (info) => { if (isCurrent() && !terminal) callbacksRef.current.onPlan?.(info) },
      onTrace: (event) => {
        if (!isCurrent() || terminal) return
        if (event.id) cursor = Math.max(cursor, event.id)
        const previous = snapshotRef.current
        if (previous) {
          updateSnapshot({
            ...previous,
            last_event_id: String(cursor),
            ...(event.event === 'step_updated' ? { current_step: Number(event.data.step) || 0 } : {}),
            ...(Number.isFinite(event.data.duration_ms) ? { duration_ms: Number(event.data.duration_ms) } : {}),
          })
        }
        callbacksRef.current.onTrace?.(event)
      },
      onApprovalRequired: (approval) => {
        if (!isCurrent() || terminal) return
        updateStatus('awaiting_approval')
        setPendingApprovals((previous) => previous.some((item) => item.id === approval.id) ? previous : [...previous, approval])
      },
      onPaused: (info) => {
        if (!isCurrent() || terminal) return
        terminal = true
        remember(info.taskId, 'awaiting_approval')
        updateStatus('awaiting_approval')
        if (info.resolvedApprovalId && info.resolvedStatus) {
          if (snapshotRef.current) updateSnapshot({ ...snapshotRef.current, pending_approvals: snapshotRef.current.pending_approvals.filter((item) => item.id !== info.resolvedApprovalId) })
          setPendingApprovals((previous) => previous.map((item) => item.id === info.resolvedApprovalId
            ? { ...item, status: info.resolvedStatus! } : item))
        }
      },
      onDone: (message) => {
        if (!isCurrent() || terminal) return
        terminal = true
        updateStatus('completed')
        setError(null)
        setPendingApprovals([])
        sessionStorage.removeItem(taskStorageKey(conversationId))
        receivedAnswer = !!message.content
        if (receivedAnswer) callbacksRef.current.onDone?.(message)
      },
      onCancelled: () => {
        if (!isCurrent() || terminal) return
        terminal = true
        updateStatus('cancelled')
        setError(null)
        setPendingApprovals([])
        sessionStorage.removeItem(taskStorageKey(conversationId))
      },
      onError: (failure) => {
        if (!isCurrent() || terminal) return
        terminal = true
        streamError = failure
        setError(failure)
        updateStatus('failed')
        if (snapshotRef.current) updateSnapshot({ ...snapshotRef.current, error_code: failure.code })
      },
    }
    try {
      await operation(handlers, controller.signal)
    } catch {
      // HTTP/SSE failure alone says nothing about the persisted task outcome.
    }
    if (!isCurrent() || controller.signal.aborted) return
    const task = snapshotRef.current
    if (terminal && task && ['completed', 'awaiting_approval'].includes(task.status)) {
      if (task.status === 'completed' && !receivedAnswer) await callbacksRef.current.onReloadMessages(conversationId)
      if (isCurrent()) { setSending(false); callbacksRef.current.onResetStream() }
      return
    }
    let latest: AssistantTaskSnapshot | null = null
    if (task?.id) latest = await chatApi.getAssistantTask(task.id).catch(() => null)
    if (!isCurrent()) return
    if (latest?.conversation_id === conversationId) {
      applySnapshot(latest)
      if (latest.status === 'completed') await callbacksRef.current.onReloadMessages(conversationId)
      if (isActiveTask(latest.status) && latest.status !== 'awaiting_approval') setDisconnected(true)
    } else if (!terminal) {
      if (task?.id) {
        setDisconnected(true)
      } else {
        setError({ code: 'assistant_unknown_error', message: '', retryable: false })
      }
    } else if (streamError) {
      setError(streamError)
    }
    if (!isCurrent()) return
    setSending(false)
    callbacksRef.current.onResetStream()
  }, [applySnapshot, updateSnapshot])

  const send = useCallback(async (conversationId: number, content: string) => {
    reset(conversationId)
    updateSnapshot(null)
    setError(null)
    setPendingApprovals([])
    sessionStorage.removeItem(taskStorageKey(conversationId))
    await runStream(conversationId, (handlers, signal) => chatApi.sendAssistantMessageStream(conversationId, content, handlers, signal))
  }, [reset, runStream, updateSnapshot])

  const restore = useCallback(async (conversationId: number, latestTask?: AssistantTaskSnapshot | null) => {
    if (conversationRef.current !== conversationId) return
    const epoch = epochRef.current
    let next = latestTask
    if (!next) {
      const storedId = Number(sessionStorage.getItem(taskStorageKey(conversationId)))
      if (!Number.isInteger(storedId) || storedId <= 0) return
      next = await chatApi.getAssistantTask(storedId).catch(() => null)
      if (!next && epoch === epochRef.current && conversationRef.current === conversationId) {
        updateSnapshot({ id: storedId, conversation_id: conversationId, status: 'running', pending_approvals: [] })
        setDisconnected(true)
        return
      }
    }
    if (!next || epochRef.current !== epoch || next.conversation_id !== conversationId || conversationRef.current !== conversationId) return
    applySnapshot(next)
    if (next.status === 'queued' || next.status === 'running') {
      // Starting after the prior attempt prevents old terminal events ending a retry.
      const cursor = Number(next.attempt_event_id) || (next.retry_count ? Number(next.last_event_id) || 0 : 0)
      await runStream(conversationId, (handlers, signal) => chatApi.subscribeAssistantTaskStream(next!.id, handlers, signal, cursor), cursor)
    }
  }, [applySnapshot, runStream, updateSnapshot])

  const reconnect = useCallback(async () => {
    const task = snapshotRef.current
    if (!task || controlRef.current) return
    const epoch = epochRef.current
    const sequence = ++controlSequenceRef.current
    controlRef.current = true
    setControl('checking')
    setControlError(false)
    try {
      const next = await chatApi.getAssistantTask(task.id)
      if (epochRef.current !== epoch || conversationRef.current !== task.conversation_id) return
      applySnapshot(next)
      setDisconnected(false)
      releaseControl(sequence)
      if (next.status === 'queued' || next.status === 'running') {
        const cursor = Number(next.attempt_event_id) || 0
        await runStream(task.conversation_id, (handlers, signal) => chatApi.subscribeAssistantTaskStream(task.id, handlers, signal, cursor), cursor)
      } else if (next.status === 'completed') {
        await callbacksRef.current.onReloadMessages(task.conversation_id)
      }
    } catch {
      if (controlSequenceRef.current === sequence && conversationRef.current === task.conversation_id) { setControlError(true); setDisconnected(true) }
    } finally {
      releaseControl(sequence)
    }
  }, [applySnapshot, runStream, releaseControl])

  const cancel = useCallback(async () => {
    const task = snapshotRef.current
    if (!task || !isActiveTask(task.status) || controlRef.current) return
    const epoch = epochRef.current
    const sequence = ++controlSequenceRef.current
    controlRef.current = true
    setControl('stopping')
    setControlError(false)
    try {
      const next = await chatApi.cancelAssistantTask(task.id)
      if (epochRef.current !== epoch || conversationRef.current !== task.conversation_id) return
      applySnapshot(next)
      if (!isActiveTask(next.status)) {
        epochRef.current += 1
        streamRef.current?.abort()
        callbacksRef.current.onResetStream()
        setSending(false)
        setDecidingApprovalId(null)
        decidingApprovalRef.current = null
        setDisconnected(false)
        if (next.status === 'completed') await callbacksRef.current.onReloadMessages(task.conversation_id)
      }
    } catch {
      if (epochRef.current === epoch) setControlError(true)
    } finally {
      releaseControl(sequence)
    }
  }, [applySnapshot, releaseControl])

  const retry = useCallback(async () => {
    const task = snapshotRef.current
    if (!task?.can_retry || controlRef.current) return
    const epoch = epochRef.current
    const sequence = ++controlSequenceRef.current
    controlRef.current = true
    setControl('retrying')
    setControlError(false)
    try {
      const next = await chatApi.retryAssistantTask(task.id)
      if (epochRef.current !== epoch || conversationRef.current !== task.conversation_id) return
      applySnapshot(next)
      releaseControl(sequence)
      if (next.status === 'queued' || next.status === 'running') {
        callbacksRef.current.onTrace?.({ event: 'retry_scheduled', data: { retry_count: next.retry_count } })
        const cursor = Number(next.last_event_id) || 0
        await runStream(task.conversation_id, (handlers, signal) => chatApi.subscribeAssistantTaskStream(task.id, handlers, signal, cursor), cursor)
      }
    } catch {
      if (controlSequenceRef.current === sequence && conversationRef.current === task.conversation_id) setControlError(true)
    } finally {
      releaseControl(sequence)
    }
  }, [applySnapshot, runStream, releaseControl])

  const decide = useCallback(async (approval: AssistantApproval, decision: 'approved' | 'rejected') => {
    const task = snapshotRef.current
    if (!task || controlRef.current || decidingApprovalRef.current || approval.status !== 'pending') throw new Error('approval_pending')
    const epoch = epochRef.current
    decidingApprovalRef.current = approval.id
    setDecidingApprovalId(approval.id)
    await runStream(task.conversation_id, (handlers, signal) => chatApi.decideAssistantApprovalStream(approval.id, decision, {
      ...handlers,
      onPaused: (info) => handlers.onPaused?.({
        ...info,
        resolvedApprovalId: info.resolvedApprovalId || approval.id,
        resolvedStatus: info.resolvedStatus || decision,
      }),
    }, task.id, signal))
    if (conversationRef.current !== task.conversation_id || epochRef.current !== epoch + 1) return
    setDecidingApprovalId(null)
    decidingApprovalRef.current = null
    // An unresolved decision can be retried only from the refreshed approval card.
    if (snapshotRef.current?.status === 'awaiting_approval') {
      const remaining = snapshotRef.current.pending_approvals.some((item) => item.id === approval.id)
      if (remaining && epochRef.current > epoch) throw new Error('approval_pending')
    }
  }, [runStream])

  return { snapshot, error, controlError, disconnected, sending, control, pendingApprovals, decidingApprovalId, reset, send, restore, reconnect, cancel, retry, decide }
}
