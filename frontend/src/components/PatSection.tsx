import { useEffect, useState } from 'react'
import { Copy, Plus, Trash2, KeyRound } from 'lucide-react'
import { patsApi, type PatItem } from '@panwatch/api'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

/**
 * MCP 访问令牌(PAT)管理。
 *
 * 令牌用于 Claude 等 MCP client 连接 PanWatch 的 MCP 端点(/mcp)。
 * 明文仅创建时返回一次;列表只显示前缀。
 */
export default function PatSection() {
  const { toast } = useToast()
  const [items, setItems] = useState<PatItem[]>([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newToken, setNewToken] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const res = await patsApi.list()
      setItems(res.items || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载令牌失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) {
      toast('请填写令牌用途备注', 'error')
      return
    }
    setCreating(true)
    try {
      const res = await patsApi.create({ name: name.trim() })
      setNewToken(res.token)
      setName('')
      await load()
      toast('令牌已创建,明文仅显示这一次,请立即保存', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '创建失败', 'error')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    try {
      await patsApi.revoke(id)
      await load()
      toast('令牌已吊销', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '吊销失败', 'error')
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text)
    toast('已复制到剪贴板', 'success')
  }

  return (
    <section id="sec-pat" className="card p-4 md:p-6 lg:col-span-12">
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" /> MCP 访问令牌
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            供 Claude 等 MCP 客户端连接本站 MCP 端点(<span className="font-mono">/mcp</span>),只读行情与持仓。明文仅创建时显示一次。
          </p>
        </div>
      </div>

      {/* 新建 */}
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <Input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="令牌用途备注,如 Claude Desktop"
          className="sm:max-w-xs"
        />
        <Button size="sm" className="h-9" onClick={create} disabled={creating}>
          <Plus className="w-3.5 h-3.5" /> 创建令牌
        </Button>
      </div>

      {/* 一次性明文展示 */}
      {newToken ? (
        <div className="mb-4 rounded-xl border border-amber-400/40 bg-amber-50/60 dark:bg-amber-950/20 p-3">
          <div className="text-[11px] text-amber-700 dark:text-amber-400 mb-1.5">
            请立即复制并妥善保存,关闭后无法再次查看:
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded bg-background/70 px-2 py-1 font-mono text-[12px]">{newToken}</code>
            <Button variant="secondary" size="sm" className="h-8" onClick={() => copy(newToken)}>
              <Copy className="w-3.5 h-3.5" /> 复制
            </Button>
            <Button variant="ghost" size="sm" className="h-8" onClick={() => setNewToken(null)}>知道了</Button>
          </div>
        </div>
      ) : null}

      {/* 列表 */}
      {loading ? (
        <div className="text-[12px] text-muted-foreground">加载中…</div>
      ) : items.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">还没有令牌。</div>
      ) : (
        <div className="space-y-2">
          {items.map(it => (
            <div
              key={it.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/40 bg-accent/20 px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-medium text-foreground truncate">{it.name || '未命名'}</span>
                  <code className="font-mono text-[11px] text-muted-foreground">{it.prefix}…</code>
                  {it.revoked ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-500/15 text-rose-600">已吊销</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600">有效</span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {it.last_used_at ? `最近使用 ${it.last_used_at.slice(0, 10)}` : '从未使用'}
                  {it.expires_at ? ` · 过期 ${it.expires_at.slice(0, 10)}` : ' · 永不过期'}
                </div>
              </div>
              {!it.revoked ? (
                <Button variant="ghost" size="sm" className="h-8 text-rose-600" onClick={() => revoke(it.id)}>
                  <Trash2 className="w-3.5 h-3.5" /> 吊销
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
