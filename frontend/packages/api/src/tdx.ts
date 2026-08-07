import { fetchAPI } from './client'
import type { TdxAskResponse } from './types'

/**
 * 通达信问小达(TDX MCP 自然语言投研)前端客户端。
 * 后端 /api/tdx/ask 直连 TDX MCP, 返回结构化表格。
 */
export const tdxApi = {
  /** 自然语言投研查询, 如 ask('今日主力净流入前10的A股', 10) */
  ask: (q: string, maxRows = 10) =>
    fetchAPI<TdxAskResponse>(
      `/tdx/ask?q=${encodeURIComponent(q)}&max_rows=${maxRows}`,
    ),
}
