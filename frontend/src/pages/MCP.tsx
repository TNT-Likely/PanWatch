import { useMemo, useState, useEffect, useRef, type ChangeEvent } from 'react'
import { ShieldCheck, KeyRound, Server, Wrench, Play, Copy, CheckCircle2, AlertCircle } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'

type JsonRpcPayload = {
  jsonrpc: '2.0'
  id: number
  method: string
  params?: Record<string, any>
}

type ToolItem = {
  name: string
  description?: string
  inputSchema?: Record<string, any>
  outputSchema?: Record<string, any>
  examples?: Array<{ title?: string; arguments?: Record<string, any> }>
}

function schemaFieldEntries(inputSchema?: Record<string, any>): Array<{ name: string; desc: string; required: boolean }> {
  const properties = (inputSchema?.properties || {}) as Record<string, any>
  const required = new Set<string>((inputSchema?.required || []) as string[])
  return Object.entries(properties).map(([name, value]) => ({
    name,
    desc: String((value as any)?.description || ''),
    required: required.has(name),
  }))
}

function outputFieldEntries(outputSchema?: Record<string, any>): Array<{ name: string; desc: string }> {
  const properties = (outputSchema?.properties || {}) as Record<string, any>
  return Object.entries(properties).map(([name, value]) => ({
    name,
    desc: String((value as any)?.description || ''),
  }))
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function toBasicAuthHeader(username: string, password: string): string {
  return `Basic ${btoa(`${username}:${password}`)}`
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fallback below
  }

  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}

export default function MCPPage() {
  const token = localStorage.getItem('token') || ''
  const hasToken = !!token
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [tools, setTools] = useState<ToolItem[]>([])
  const [resultText, setResultText] = useState('')
  const [errorText, setErrorText] = useState('')
  const [copied, setCopied] = useState(false)
  const [copiedTemplate, setCopiedTemplate] = useState<'bearer' | 'basic' | null>(null)
  const autoConnectRef = useRef(false)

  const endpoint = '/api/mcp'

  const toolGroups = useMemo(() => {
    const groups: Record<string, ToolItem[]> = {}
    for (const t of tools) {
      const key = (t.name.split('.')[0] || 'other').toUpperCase()
      if (!groups[key]) groups[key] = []
      groups[key].push(t)
    }
    return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]))
  }, [tools])

  const callMcp = async (payload: JsonRpcPayload) => {
    let authHeader = ''
    if (username && password) {
      authHeader = toBasicAuthHeader(username, password)
    } else if (token) {
      authHeader = `Bearer ${token}`
    } else {
      throw new Error('请先登录，或填写 MCP 用户名和密码')
    }

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: authHeader,
      },
      body: JSON.stringify(payload),
    })

    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const msg = data?.detail || data?.message || `HTTP ${res.status}`
      throw new Error(msg)
    }
    return data
  }

  const handleConnect = async () => {
    setLoading(true)
    setErrorText('')
    setResultText('')
    try {
      const initResp = await callMcp({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} })
      const listResp = await callMcp({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} })
      const parsedTools = (listResp?.result?.tools || []) as ToolItem[]
      setTools(parsedTools)
      setResultText(pretty({ initialize: initResp, tools_count: parsedTools.length }))
    } catch (e: any) {
      setErrorText(e?.message || '连接失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (autoConnectRef.current) return
    if (!hasToken) return
    autoConnectRef.current = true
    handleConnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasToken])

  const handleQuickCall = async (name: string, argumentsValue: Record<string, any>) => {
    setLoading(true)
    setErrorText('')
    setResultText('')
    try {
      const data = await callMcp({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tools/call',
        params: { name, arguments: argumentsValue },
      })
      setResultText(pretty(data))
    } catch (e: any) {
      setErrorText(e?.message || '调用失败')
    } finally {
      setLoading(false)
    }
  }

  const copyEndpoint = async () => {
    const fullEndpoint = `${window.location.origin}${endpoint}`
    const ok = await copyText(fullEndpoint)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
      return
    }
    setErrorText('复制失败：请手动复制地址，或使用 HTTPS/localhost 环境')
  }

  const bearerTemplate = `{
  "mcpServers": {
    "panwatch": {
      "description": "PanWatch 投资分析 MCP：支持自选股、持仓、K线、新闻、价格提醒、基金、AI Agent 等 40+ 工具",
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8000/api/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}`

  const basicTemplate = `{
  "mcpServers": {
    "panwatch": {
      "description": "PanWatch 投资分析 MCP：支持自选股、持仓、K线、新闻、价格提醒、基金、AI Agent 等 40+ 工具",
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8000/api/mcp",
      "headers": {
        "Authorization": "Basic <base64(username:password)>"
      }
    }
  }
}`

  const copyTemplate = async (name: 'bearer' | 'basic', content: string) => {
    const ok = await copyText(content)
    if (ok) {
      setCopiedTemplate(name)
      setTimeout(() => setCopiedTemplate(null), 1200)
      return
    }
    setErrorText('复制失败：请手动复制模板内容，或使用 HTTPS/localhost 环境')
  }

  return (
    <div className="space-y-6">
      <div className="card p-5 md:p-6">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <h1 className="text-[20px] md:text-[24px] font-bold text-foreground tracking-tight">MCP 接口中心</h1>
            <p className="text-[13px] text-muted-foreground mt-1">
              PanWatch 提供 40+ MCP 工具，覆盖自选股、持仓、K线、新闻、价格提醒、基金、AI Agent 等功能。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="px-3 py-2 rounded-xl bg-accent/30 border border-border/50 text-[12px] font-mono">{endpoint}</div>
            <Button variant="outline" size="sm" onClick={copyEndpoint}>
              {copied ? <CheckCircle2 className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {copied ? '已复制' : '复制地址'}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card p-5 xl:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <KeyRound className="w-4 h-4 text-primary" />
            <h2 className="text-[14px] font-semibold">MCP 连接</h2>
          </div>

          <div className="space-y-3">
            {hasToken ? (
              <div className="text-[12px] text-muted-foreground bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-3">
                已检测到登录态 Token，将自动使用 Bearer 鉴权。
              </div>
            ) : (
              <>
                <div>
                  <label className="text-[12px] text-muted-foreground block mb-1">用户名</label>
                  <input
                    value={username}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setUsername(e.target.value)}
                    placeholder="请输入 MCP 用户名"
                    className="w-full h-10 rounded-xl border border-border bg-background px-3 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </div>
                <div>
                  <label className="text-[12px] text-muted-foreground block mb-1">密码</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                    placeholder="请输入 MCP 密码"
                    className="w-full h-10 rounded-xl border border-border bg-background px-3 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </div>
              </>
            )}

            <Button onClick={handleConnect} disabled={loading} className="w-full">
              <ShieldCheck className="w-4 h-4" />
              {loading ? '连接中...' : '连接并拉取工具'}
            </Button>

            <div className="text-[12px] text-muted-foreground bg-accent/20 border border-border/50 rounded-xl p-3">
              认证方式: 优先 Bearer（当前登录态），也支持 HTTP Basic。
            </div>
          </div>
        </div>

        <div className="card p-5 xl:col-span-2">
          <div className="flex items-center gap-2 mb-4">
            <Wrench className="w-4 h-4 text-primary" />
            <h2 className="text-[14px] font-semibold">可用 MCP 工具</h2>
            <span className="text-[12px] text-muted-foreground">{tools.length > 0 ? `${tools.length} 个` : ''}</span>
          </div>

          {tools.length === 0 ? (
            <div className="text-[13px] text-muted-foreground h-[180px] rounded-xl border border-dashed border-border flex items-center justify-center">
              先填写账号密码并点击“连接并拉取工具”
            </div>
          ) : (
            <div className="space-y-4 max-h-[420px] overflow-auto pr-1">
              {toolGroups.map(([group, items]) => (
                <div key={group}>
                  <div className="text-[12px] font-semibold text-primary mb-2">{group}</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {items.map((t) => (
                      <div key={t.name} className="rounded-xl border border-border/60 bg-background/70 p-3">
                        <div className="text-[12px] font-mono text-foreground">{t.name}</div>
                        <div className="text-[12px] text-muted-foreground mt-1 line-clamp-2">{t.description || '无说明'}</div>
                        {schemaFieldEntries(t.inputSchema).length > 0 && (
                          <div className="mt-2 space-y-1">
                            <div className="text-[11px] text-primary/80">输入参数</div>
                            {schemaFieldEntries(t.inputSchema).map((f) => (
                              <div key={`${t.name}-${f.name}`} className="text-[11px] leading-4 text-muted-foreground">
                                <span className="font-mono text-foreground/90">{f.name}</span>
                                {f.required ? <span className="ml-1 text-rose-500">*</span> : null}
                                {f.desc ? <span className="ml-1">- {f.desc}</span> : null}
                              </div>
                            ))}
                          </div>
                        )}

                        {outputFieldEntries(t.outputSchema).length > 0 && (
                          <div className="mt-2 space-y-1">
                            <div className="text-[11px] text-primary/80">输出字段</div>
                            {outputFieldEntries(t.outputSchema).map((f) => (
                              <div key={`${t.name}-out-${f.name}`} className="text-[11px] leading-4 text-muted-foreground">
                                <span className="font-mono text-foreground/90">{f.name}</span>
                                {f.desc ? <span className="ml-1">- {f.desc}</span> : null}
                              </div>
                            ))}
                          </div>
                        )}

                        {(t.examples || []).length > 0 && (
                          <div className="mt-2 space-y-1">
                            <div className="text-[11px] text-primary/80">调用示例</div>
                            {(t.examples || []).slice(0, 2).map((example, idx) => (
                              <div key={`${t.name}-ex-${idx}`} className="text-[11px] leading-4 text-muted-foreground">
                                <span>{example.title || `示例 ${idx + 1}`}</span>
                                <pre className="mt-1 text-[10px] leading-4 font-mono rounded bg-accent/30 p-2 overflow-auto">{pretty(example.arguments || {})}</pre>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 2xl:grid-cols-3 gap-4">
        <div className="card p-5 2xl:col-span-1">
          <div className="flex items-center gap-2 mb-3">
            <Play className="w-4 h-4 text-primary" />
            <h2 className="text-[14px] font-semibold">快捷调用</h2>
          </div>
          <div className="space-y-2 max-h-[400px] overflow-auto pr-1">
            <div className="text-[11px] text-muted-foreground mb-1">基础数据</div>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('stocks.list', {})} disabled={loading}>
              自选股列表
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('stocks.quotes', {})} disabled={loading}>
              自选股行情
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('portfolio.summary', { include_quotes: false })} disabled={loading}>
              持仓汇总
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('market.indices', {})} disabled={loading}>
              大盘指数
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('dashboard.overview', { market: 'ALL', action_limit: 6, risk_limit: 6, days: 45 })} disabled={loading}>
              Dashboard 概览
            </Button>
            <div className="text-[11px] text-muted-foreground mt-3 mb-1">管理工具</div>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('accounts.list', {})} disabled={loading}>
              账户列表
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('agents.list', {})} disabled={loading}>
              Agent 列表
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('agents.health', {})} disabled={loading}>
              Agent 健康状态
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('mcp.logs.query', { limit: 20 })} disabled={loading}>
              MCP审计日志
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('price_alerts.list', {})} disabled={loading}>
              价格提醒列表
            </Button>
            <div className="text-[11px] text-muted-foreground mt-3 mb-1">数据查询</div>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('news.list', { limit: 10 })} disabled={loading}>
              最新新闻
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('suggestions.latest', { limit: 5 })} disabled={loading}>
              最新推荐
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('history.list', { limit: 5 })} disabled={loading}>
              分析历史
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => handleQuickCall('exchange_rates.get', {})} disabled={loading}>
              汇率数据
            </Button>
          </div>
        </div>

        <div className="card p-5 2xl:col-span-2">
          <div className="flex items-center gap-2 mb-3">
            <Server className="w-4 h-4 text-primary" />
            <h2 className="text-[14px] font-semibold">返回结果</h2>
          </div>

          {errorText ? (
            <div className="mb-3 flex items-start gap-2 text-[12px] text-rose-500 bg-rose-500/10 border border-rose-500/20 rounded-xl p-3">
              <AlertCircle className="w-4 h-4 mt-0.5" />
              <span>{errorText}</span>
            </div>
          ) : null}

          <pre className="text-[12px] leading-5 font-mono rounded-xl bg-black/90 text-emerald-300 p-4 overflow-auto h-[360px]">
            {resultText || '调用结果会显示在这里'}
          </pre>
        </div>
      </div>

      <div className="card p-5">
        <h2 className="text-[14px] font-semibold mb-2">MCP 传输类型说明</h2>
        <div className="text-[12px] text-muted-foreground space-y-1">
          <p>当前 PanWatch MCP 使用 <span className="font-medium text-foreground">JSON-RPC over HTTP</span>。</p>
          <p>类型上属于 <span className="font-medium text-foreground">HTTP 接口调用</span>，不是 SSE 推送通道。</p>
          <p>每次请求都通过 POST 到 <span className="font-mono text-foreground">/api/mcp</span>，返回标准 JSON-RPC 结果。</p>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[14px] font-semibold">MCP JSON 模板（Bearer）</h2>
            <Button variant="outline" size="sm" onClick={() => copyTemplate('bearer', bearerTemplate)}>
              {copiedTemplate === 'bearer' ? '已复制' : '复制'}
            </Button>
          </div>
          <p className="text-[12px] text-muted-foreground mb-2">适合已登录态或支持 Bearer Header 的客户端。</p>
          <div className="text-[11px] text-muted-foreground mb-2 space-y-0.5">
            <p><span className="font-mono text-foreground">url</span>：MCP HTTP 地址。</p>
            <p><span className="font-mono text-foreground">headers.Authorization</span>：使用登录 Token，格式 <span className="font-mono text-foreground">Bearer &lt;token&gt;</span>。</p>
            <p>传输类型：HTTP JSON-RPC（非 SSE）。</p>
          </div>
          <pre className="text-[12px] leading-5 font-mono rounded-xl bg-black/90 text-emerald-300 p-4 overflow-auto h-[220px]">{bearerTemplate}</pre>
        </div>

        <div className="card p-5">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[14px] font-semibold">MCP JSON 模板（Basic）</h2>
            <Button variant="outline" size="sm" onClick={() => copyTemplate('basic', basicTemplate)}>
              {copiedTemplate === 'basic' ? '已复制' : '复制'}
            </Button>
          </div>
          <p className="text-[12px] text-muted-foreground mb-2">配置外部 MCP 客户端时可用，需替换为你的用户名密码 Base64。</p>
          <div className="text-[11px] text-muted-foreground mb-2 space-y-0.5">
            <p><span className="font-mono text-foreground">url</span>：MCP HTTP 地址。</p>
            <p><span className="font-mono text-foreground">headers.Authorization</span>：格式 <span className="font-mono text-foreground">Basic &lt;base64(username:password)&gt;</span>。</p>
            <p>传输类型：HTTP JSON-RPC（非 SSE）。</p>
          </div>
          <pre className="text-[12px] leading-5 font-mono rounded-xl bg-black/90 text-emerald-300 p-4 overflow-auto h-[220px]">{basicTemplate}</pre>
        </div>
      </div>
    </div>
  )
}
