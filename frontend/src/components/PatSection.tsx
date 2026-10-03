import { useEffect, useState } from 'react'
import { Copy, Plus, Trash2, KeyRound } from 'lucide-react'
import { patsApi, type PatItem } from '@panwatch/api'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import { formatDate } from '@/i18n/format'

/**
 * MCP 访问令牌(PAT)管理。
 *
 * 令牌用于 Claude 等 MCP client 连接 PanWatch 的 MCP 端点(/mcp)。
 * 明文仅创建时返回一次;列表只显示前缀。
 */
export default function PatSection({ className = 'lg:col-span-12' }: { className?: string }) {
  const { t } = useTranslation(['configuration', 'common'])
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
      toast(e instanceof Error ? e.message : t('configuration:pat.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) {
      toast(t('configuration:pat.nameRequired'), 'error')
      return
    }
    setCreating(true)
    try {
      const res = await patsApi.create({ name: name.trim() })
      setNewToken(res.token)
      setName('')
      await load()
      toast(t('configuration:pat.created'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('configuration:pat.createFailed'), 'error')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    try {
      await patsApi.revoke(id)
      await load()
      toast(t('configuration:pat.revoked'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('configuration:pat.revokeFailed'), 'error')
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text)
    toast(t('configuration:pat.copied'), 'success')
  }

  return (
    <section id="sec-pat" className={`card p-4 md:p-6 ${className}`}>
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" /> {t('configuration:pat.title')}
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            {t('configuration:pat.descriptionBeforeEndpoint')}<span className="font-mono">/mcp</span>{t('configuration:pat.descriptionAfterEndpoint')}
          </p>
        </div>
      </div>

      {/* 新建 */}
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <Input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder={t('configuration:pat.namePlaceholder')}
          className="sm:max-w-xs"
        />
        <Button size="sm" className="h-9" onClick={create} disabled={creating}>
          <Plus className="w-3.5 h-3.5" /> {t('configuration:pat.create')}
        </Button>
      </div>

      {/* 一次性明文展示 */}
      {newToken ? (
        <div className="mb-4 rounded-xl border border-amber-400/40 bg-amber-50/60 dark:bg-amber-950/20 p-3">
          <div className="text-[11px] text-amber-700 dark:text-amber-400 mb-1.5">
            {t('configuration:pat.saveNow')}
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded bg-background/70 px-2 py-1 font-mono text-[12px]">{newToken}</code>
            <Button variant="secondary" size="sm" className="h-8" onClick={() => copy(newToken)}>
              <Copy className="w-3.5 h-3.5" /> {t('common:actions.copy')}
            </Button>
            <Button variant="ghost" size="sm" className="h-8" onClick={() => setNewToken(null)}>{t('configuration:pat.understood')}</Button>
          </div>
        </div>
      ) : null}

      {/* 列表 */}
      {loading ? (
        <div className="text-[12px] text-muted-foreground">{t('common:states.loading')}</div>
      ) : items.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">{t('configuration:pat.empty')}</div>
      ) : (
        <div className="space-y-2">
          {items.map(it => (
            <div
              key={it.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/40 bg-accent/20 px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-medium text-foreground truncate">{it.name || t('configuration:pat.unnamed')}</span>
                  <code className="font-mono text-[11px] text-muted-foreground">{it.prefix}…</code>
                  {it.revoked ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-500/15 text-rose-600">{t('configuration:pat.statusRevoked')}</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600">{t('configuration:pat.statusValid')}</span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {it.last_used_at ? t('configuration:pat.lastUsed', { date: formatDate(it.last_used_at) }) : t('configuration:pat.neverUsed')}
                  {it.expires_at ? t('configuration:pat.expires', { date: formatDate(it.expires_at) }) : t('configuration:pat.neverExpires')}
                </div>
              </div>
              {!it.revoked ? (
                <Button variant="ghost" size="sm" className="h-8 text-rose-600" onClick={() => revoke(it.id)}>
                  <Trash2 className="w-3.5 h-3.5" /> {t('configuration:pat.revoke')}
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
