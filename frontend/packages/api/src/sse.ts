// SSE 客户端基建：基于 fetch + ReadableStream（原生 EventSource 无法带 Authorization header）
// - readSSE: 单次连接，流结束后 resolve；连接失败直接 reject（调用方据此降级轮询/非流式）
// - subscribeSSE: 自动重连订阅（带 Last-Event-ID 续推），用于进度/日志等 GET 流
import { getToken } from './client'

export interface SSEEvent {
  /** 事件序号（服务端自增，断线重连用） */
  id: number
  event: string
  /** data 行 JSON.parse 后的结果；解析失败时为原始字符串 */
  data: any
}

export interface ReadSSEOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  /** 断线重连时带上，服务端从其后续推 */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
}

/** 解析一段 SSE wire 文本块（不含结尾空行分隔符） */
function parseEventBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  if (lines.every((l) => !l || l.startsWith(':'))) return null // 心跳注释
  let id = 0
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('id: ')) id = parseInt(line.slice(4), 10) || 0
    else if (line.startsWith('event: ')) event = line.slice(7)
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    else if (line === 'data:') dataLines.push('')
  }
  const raw = dataLines.join('\n')
  let data: any = raw
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      /* 保留原始字符串 */
    }
  }
  return { id, event, data }
}

/**
 * 建立一次 SSE 连接并消费到流结束。
 * 返回本次收到的最后事件序号（供调用方断线重连续推）。
 * 连接失败（HTTP 非 2xx / content-type 不对 / 网络错误）时抛异常。
 */
export async function readSSE(path: string, options: ReadSSEOptions): Promise<{ lastEventId: number }> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.lastEventId && options.lastEventId > 0) {
    headers['Last-Event-ID'] = String(options.lastEventId)
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })
  if (!res.ok) throw new Error(`SSE HTTP ${res.status}`)
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) throw new Error(`非 SSE 响应: ${contentType}`)
  if (!res.body) throw new Error('SSE 响应无 body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventId = options.lastEventId || 0

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // 事件之间以空行分隔
    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      const ev = parseEventBlock(block)
      if (ev) {
        if (ev.id > 0) lastEventId = ev.id
        options.onEvent(ev)
      }
    }
  }
  return { lastEventId }
}

export interface SubscribeSSEOptions {
  /** 首次连接的续推起点（如已知的最大日志 id） */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
  /** 每次（重）连接成功前触发，可用于 UI 状态 */
  onRetry?: (attempt: number) => void
  /** 重试次数用尽后触发（调用方降级轮询） */
  onFailed?: (err: unknown) => void
  maxRetries?: number
}

/**
 * 自动重连的 SSE 订阅（GET）。断线按指数退避重连并带 Last-Event-ID 续推。
 * 返回取消函数；服务端正常关流（收到 done 事件后调用方主动 close）或重试用尽后停止。
 */
export function subscribeSSE(path: string, options: SubscribeSSEOptions): () => void {
  const controller = new AbortController()
  let closed = false
  let lastEventId = options.lastEventId || 0
  const maxRetries = options.maxRetries ?? 5

  const loop = async () => {
    let attempt = 0
    while (!closed) {
      try {
        const { lastEventId: newId } = await readSSE(path, {
          signal: controller.signal,
          lastEventId,
          onEvent: (ev) => {
            if (ev.id > 0) lastEventId = ev.id
            attempt = 0 // 收到数据即重置重试计数
            options.onEvent(ev)
          },
        })
        lastEventId = newId
        // 服务端正常关流（如超时 done）：由调用方决定是否重订阅，这里退出
        return
      } catch (err) {
        if (closed || controller.signal.aborted) return
        attempt += 1
        if (attempt > maxRetries) {
          options.onFailed?.(err)
          return
        }
        options.onRetry?.(attempt)
        // 指数退避：1s/2s/4s/8s/8s...
        const delay = Math.min(1000 * 2 ** (attempt - 1), 8000)
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
  void loop()

  return () => {
    closed = true
    controller.abort()
  }
}
