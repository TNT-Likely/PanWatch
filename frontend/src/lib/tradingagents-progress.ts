export const TERMINAL_PROGRESS_STATUSES = ['success', 'failed', 'stale'] as const

export type TerminalProgressStatus = typeof TERMINAL_PROGRESS_STATUSES[number]

export function isTerminalProgressStatus(status: string | null | undefined): status is TerminalProgressStatus {
  return TERMINAL_PROGRESS_STATUSES.includes(status as TerminalProgressStatus)
}

/**
 * SSE 关闭只代表这条连接结束，不等于任务结束。
 * not_found、running、timeout 和空状态都应该交给 polling 接力。
 */
export function shouldContinueProgressWatch(
  status: string | null | undefined,
  event: 'progress' | 'done' = 'progress',
): boolean {
  void event
  return !isTerminalProgressStatus(status)
}
