import { useState, useEffect, useRef } from 'react'
import { Check, Eye, EyeOff, Plus, Pencil, Trash2, Star, Send, Cpu, Play, Download, Upload, FileJson, BarChart3 } from 'lucide-react'
import { fetchAPI, type AIService, type AIModel, type NotifyChannel } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { useToast } from '@/components/ui/toast'

interface Setting {
  key: string
  value: string
  description: string
}

interface TemplatePayload {
  version: number
  exported_at?: string
  settings?: Record<string, string>
  agents?: any[]
  stocks?: any[]
}

interface FeedbackStats {
  range_days: number
  total: number
  useful: number
  useless: number
  useful_rate: number
  by_day: Array<{ day: string; total: number; useful: number; useless: number; useful_rate: number }>
  by_agent: Array<{ agent_name: string; total: number; useful: number; useless: number; useful_rate: number }>
}

interface ServiceForm {
  name: string
  base_url: string
  api_key: string
}

interface ModelForm {
  name: string
  service_id: number | null
  model: string
}

interface ChannelForm {
  name: string
  type: string
  config: Record<string, string>
}

interface ChannelFieldDef {
  key: string
  label: string
  placeholder: string
  secret?: boolean
  required?: boolean
}

const CHANNEL_TYPE_FIELDS: Record<string, { label: string; fields: ChannelFieldDef[] }> = {
  telegram: {
    label: 'Telegram',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: '123456:ABC-DEF...', secret: true, required: true },
      { key: 'chat_id', label: 'Chat ID', placeholder: '-100123456789', required: true },
      { key: 'proxy', label: '代理', placeholder: 'http://192.168.1.1:7890 或 socks5://...' },
    ],
  },
  bark: {
    label: 'Bark',
    fields: [
      { key: 'device_key', label: 'Device Key', placeholder: '你的 Bark Device Key', required: true },
      { key: 'server_url', label: '服务器地址', placeholder: '默认 api.day.app，自建可填' },
    ],
  },
  dingtalk: {
    label: '钉钉机器人',
    fields: [
      { key: 'token', label: 'Webhook Token', placeholder: 'access_token 值', secret: true, required: true },
      { key: 'secret', label: '加签密钥', placeholder: 'SEC... (选填)', secret: true },
      { key: 'phones', label: '@手机号', placeholder: '逗号分隔，如 13800138000,13900139000' },
      { key: 'keyword', label: '关键字', placeholder: '若群机器人启用“关键字”，填入以自动附加' },
    ],
  },
  wecom: {
    label: '企业微信机器人',
    fields: [
      { key: 'webhook_key', label: 'Webhook Key', placeholder: 'Webhook URL 中 key= 后的值', secret: true, required: true },
    ],
  },
  lark: {
    label: '飞书机器人',
    fields: [
      { key: 'webhook_token', label: 'Webhook Token', placeholder: 'hook/ 后面的 token', secret: true, required: true },
    ],
  },
  serverchan: {
    label: 'Server酱',
    fields: [
      { key: 'sendkey', label: 'SendKey', placeholder: 'SCT...', secret: true, required: true },
    ],
  },
  pushplus: {
    label: 'PushPlus',
    fields: [
      { key: 'token', label: 'Token', placeholder: '你的 PushPlus Token', secret: true, required: true },
      { key: 'topic', label: '群组编码', placeholder: '选填，群组推送时填写' },
    ],
  },
  discord: {
    label: 'Discord',
    fields: [
      { key: 'webhook_id', label: 'Webhook ID', placeholder: 'Webhook URL 中的 ID', required: true },
      { key: 'webhook_token', label: 'Webhook Token', placeholder: 'Webhook URL 中的 Token', secret: true, required: true },
    ],
  },
  pushover: {
    label: 'Pushover',
    fields: [
      { key: 'user_key', label: 'User Key', placeholder: '用户 Key', required: true },
      { key: 'app_token', label: 'App Token', placeholder: '应用 Token', secret: true, required: true },
    ],
  },
}

const emptyServiceForm: ServiceForm = { name: '', base_url: '', api_key: '' }
const emptyModelForm: ModelForm = { name: '', service_id: null, model: '' }
const emptyChannelForm: ChannelForm = { name: '', type: 'telegram', config: {} }

export default function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [version, setVersion] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  // Service dialog
  const [serviceDialogOpen, setServiceDialogOpen] = useState(false)
  const [serviceForm, setServiceForm] = useState<ServiceForm>(emptyServiceForm)
  const [editServiceId, setEditServiceId] = useState<number | null>(null)
  const [serviceKeyVisible, setServiceKeyVisible] = useState(false)

  // Model dialog
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm)
  const [editModelId, setEditModelId] = useState<number | null>(null)

  // Channel dialog
  const [channelDialogOpen, setChannelDialogOpen] = useState(false)
  const [channelForm, setChannelForm] = useState<ChannelForm>(emptyChannelForm)
  const [editChannelId, setEditChannelId] = useState<number | null>(null)
  const [channelKeyVisible, setChannelKeyVisible] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [testingModel, setTestingModel] = useState<number | null>(null)

  // Templates (config pack)
  const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)

  // Feedback stats
  const [fbStats, setFbStats] = useState<FeedbackStats | null>(null)
  const [fbLoading, setFbLoading] = useState(false)

  const importFileRef = useRef<HTMLInputElement | null>(null)

  const { toast } = useToast()

  const builtinTemplates: Array<{ name: string; desc: string; payload: TemplatePayload }> = [
    {
      name: '保守',
      desc: '低打扰：盘中更严格触发，静默时段建议开启',
      payload: {
        version: 1,
        settings: {
          notify_quiet_hours: '23:00-07:00',
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'news_digest', enabled: true, schedule: '0 */3 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/10 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 4.0, volume_alert_ratio: 2.5, throttle_minutes: 45 } },
        ],
      },
    },
    {
      name: '均衡',
      desc: '默认推荐：兼顾覆盖与打扰',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'news_digest', enabled: true, schedule: '0 */2 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/5 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 3.0, volume_alert_ratio: 2.0, throttle_minutes: 30 } },
        ],
      },
    },
    {
      name: '激进',
      desc: '更高频：更早捕捉变化，适合短线盯盘',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '3',
          notify_retry_backoff_seconds: '1',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '10 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '10 15 * * 1-5', execution_mode: 'batch' },
          { name: 'news_digest', enabled: true, schedule: '0 * * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/3 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 2.0, volume_alert_ratio: 1.8, throttle_minutes: 20 } },
        ],
      },
    },
  ]

  const load = async () => {
    try {
      const [settingsData, servicesData, channelsData, versionData] = await Promise.all([
        fetchAPI<Setting[]>('/settings'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
        fetchAPI<{ version: string }>('/settings/version'),
      ])
      setSettings(settingsData)
      setServices(servicesData)
      setChannels(channelsData)
      setVersion(versionData.version)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const downloadJson = (name: string, obj: any) => {
    try {
      const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    }
  }

  const exportTemplate = async () => {
    setExporting(true)
    try {
      const data = await fetchAPI<TemplatePayload>('/templates/export')
      const date = new Date().toISOString().slice(0, 10)
      downloadJson(`panwatch-config-${date}.json`, data)
      toast('配置包已导出', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '导出失败', 'error')
    } finally {
      setExporting(false)
    }
  }

  const importTemplate = async (payload: TemplatePayload) => {
    setImporting(true)
    try {
      const resp = await fetchAPI<any>(`/templates/import?mode=${importMode}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      toast('配置包已导入', 'success')
      // refresh
      await load()
      return resp
    } catch (e) {
      toast(e instanceof Error ? e.message : '导入失败', 'error')
      return null
    } finally {
      setImporting(false)
    }
  }

  const loadFeedbackStats = async () => {
    setFbLoading(true)
    try {
      const stats = await fetchAPI<FeedbackStats>('/feedback/stats?days=14')
      setFbStats(stats)
    } catch (e) {
      console.error(e)
      setFbStats(null)
    } finally {
      setFbLoading(false)
    }
  }

  useEffect(() => { load(); loadFeedbackStats() }, [])

  const handleSave = async (key: string) => {
    setSaving(key)
    try {
      await fetchAPI(`/settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value: edited[key] ?? settings.find(s => s.key === key)?.value }),
      })
      const newEdited = { ...edited }
      delete newEdited[key]
      setEdited(newEdited)
      setSaved(key)
      setTimeout(() => setSaved(null), 2000)
      load()
    } catch {
      toast('保存失败', 'error')
    } finally {
      setSaving(null)
    }
  }

  // Service CRUD
  const openServiceDialog = (svc?: AIService) => {
    if (svc) {
      setServiceForm({ name: svc.name, base_url: svc.base_url, api_key: svc.api_key })
      setEditServiceId(svc.id)
    } else {
      setServiceForm(emptyServiceForm)
      setEditServiceId(null)
    }
    setServiceKeyVisible(false)
    setServiceDialogOpen(true)
  }

  const saveService = async () => {
    try {
      if (editServiceId) {
        await fetchAPI(`/providers/services/${editServiceId}`, { method: 'PUT', body: JSON.stringify(serviceForm) })
      } else {
        await fetchAPI('/providers/services', { method: 'POST', body: JSON.stringify(serviceForm) })
      }
      setServiceDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const deleteService = async (id: number) => {
    if (!confirm('删除服务商将同时删除其下所有模型，确定？')) return
    try {
      await fetchAPI(`/providers/services/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  // Model CRUD
  const openModelDialog = (serviceId?: number, model?: AIModel) => {
    if (model) {
      setModelForm({ name: model.name, service_id: model.service_id, model: model.model })
      setEditModelId(model.id)
    } else {
      setModelForm({ ...emptyModelForm, service_id: serviceId ?? null })
      setEditModelId(null)
    }
    setModelDialogOpen(true)
  }

  const saveModel = async () => {
    try {
      if (editModelId) {
        await fetchAPI(`/providers/models/${editModelId}`, { method: 'PUT', body: JSON.stringify(modelForm) })
      } else {
        await fetchAPI('/providers/models', { method: 'POST', body: JSON.stringify(modelForm) })
      }
      setModelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const deleteModel = async (id: number) => {
    if (!confirm('确定删除此模型？')) return
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  const setDefaultModel = async (id: number) => {
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('设置失败', 'error')
    }
  }

  const testModel = async (id: number) => {
    setTestingModel(id)
    try {
      await fetchAPI(`/providers/models/${id}/test`, { method: 'POST' })
      toast('模型测试成功', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTestingModel(null)
    }
  }

  // Channel CRUD
  const openChannelDialog = (channel?: NotifyChannel) => {
    if (channel) {
      setChannelForm({
        name: channel.name,
        type: channel.type,
        config: channel.config ? { ...channel.config } : {},
      })
      setEditChannelId(channel.id)
    } else {
      setChannelForm(emptyChannelForm)
      setEditChannelId(null)
    }
    setChannelKeyVisible(false)
    setChannelDialogOpen(true)
  }

  const saveChannel = async () => {
    const payload = {
      name: channelForm.name,
      type: channelForm.type,
      config: channelForm.config,
    }
    try {
      if (editChannelId) {
        await fetchAPI(`/channels/${editChannelId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/channels', { method: 'POST', body: JSON.stringify(payload) })
      }
      setChannelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const isChannelFormValid = () => {
    if (!channelForm.name) return false
    const typeDef = CHANNEL_TYPE_FIELDS[channelForm.type]
    if (!typeDef) return false
    return typeDef.fields
      .filter(f => f.required)
      .every(f => !!channelForm.config[f.key]?.trim())
  }

  const deleteChannel = async (id: number) => {
    if (!confirm('确定删除此通知渠道？')) return
    try {
      await fetchAPI(`/channels/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  const setDefaultChannel = async (id: number) => {
    try {
      await fetchAPI(`/channels/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('设置失败', 'error')
    }
  }

  const toggleChannelEnabled = async (channel: NotifyChannel) => {
    try {
      await fetchAPI(`/channels/${channel.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !channel.enabled }) })
      load()
    } catch {
      toast('操作失败', 'error')
    }
  }

  const testChannel = async (id: number) => {
    setTesting(id)
    try {
      await fetchAPI(`/channels/${id}/test`, { method: 'POST' })
      toast('测试通知已发送', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTesting(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 md:mb-8">
        <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">设置</h1>
        <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">AI 服务商、模型、通知渠道与系统配置</p>
      </div>

      <div className="space-y-6">
        {/* AI Services + Models Section */}
        <section className="card p-4 md:p-6">
          <div className="flex items-center justify-between mb-4 md:mb-5">
            <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">AI 服务商 & 模型</h3>
            <Button size="sm" className="h-8" onClick={() => openServiceDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">添加服务商</span>
            </Button>
          </div>
          {services.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">暂无 AI 服务商，点击"添加服务商"创建</p>
          ) : (
            <div className="space-y-4">
              {services.map(svc => (
                <div key={svc.id} className="rounded-xl bg-accent/30 overflow-hidden">
                  {/* Service header */}
                  <div className="flex items-center justify-between p-3.5">
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{svc.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5 truncate font-mono">{svc.base_url}</p>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <Button size="sm" variant="ghost" className="h-7 text-[11px]" onClick={() => openModelDialog(svc.id)}>
                        <Plus className="w-3 h-3" /> 模型
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openServiceDialog(svc)}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteService(svc.id)}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                  {/* Models under this service */}
                  {svc.models.length > 0 && (
                    <div className="px-3.5 pb-3.5 space-y-1.5">
                      {svc.models.map(m => (
                        <div key={m.id} className="flex items-center justify-between px-3 py-2 rounded-lg bg-background/60">
                          <div className="flex items-center gap-2">
                            {m.is_default && <Star className="w-3 h-3 text-amber-500" />}
                            <Cpu className="w-3 h-3 text-muted-foreground" />
                            <span className="text-[12px] font-medium text-foreground">{m.name}</span>
                            <span className="text-[11px] text-muted-foreground font-mono">{m.model}</span>
                          </div>
                          <div className="flex items-center gap-0.5">
                            <Button
                              variant="ghost" size="icon" className="h-6 w-6"
                              onClick={() => testModel(m.id)}
                              disabled={testingModel === m.id}
                              title="测试模型"
                            >
                              {testingModel === m.id ? (
                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                              ) : (
                                <Play className="w-3 h-3" />
                              )}
                            </Button>
                            {!m.is_default && (
                              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setDefaultModel(m.id)} title="设为默认">
                                <Star className="w-3 h-3" />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => openModelDialog(svc.id, m)}>
                              <Pencil className="w-3 h-3" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-6 w-6 hover:text-destructive" onClick={() => deleteModel(m.id)}>
                              <Trash2 className="w-3 h-3" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Notify Channel Section */}
        <section className="card p-4 md:p-6">
          <div className="flex items-center justify-between mb-4 md:mb-5">
            <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">通知渠道</h3>
            <Button size="sm" className="h-8" onClick={() => openChannelDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">添加</span>
            </Button>
          </div>
          {channels.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">暂无通知渠道，点击"添加"创建</p>
          ) : (
            <div className="space-y-3">
              {channels.map(ch => (
                <div key={ch.id} className="flex items-center justify-between p-3.5 rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    {ch.is_default && <Star className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" />}
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{ch.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{CHANNEL_TYPE_FIELDS[ch.type]?.label || ch.type}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => testChannel(ch.id)}
                      disabled={testing === ch.id || !ch.enabled}
                      title="发送测试"
                    >
                      {testing === ch.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Send className="w-3.5 h-3.5" />
                      )}
                    </Button>
                    {!ch.is_default && (
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDefaultChannel(ch.id)} title="设为默认">
                        <Star className="w-3.5 h-3.5" />
                      </Button>
                    )}
                    <Switch checked={ch.enabled} onCheckedChange={() => toggleChannelEnabled(ch)} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openChannelDialog(ch)}>
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteChannel(ch.id)}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Config Pack (Templates) */}
        <section className="card p-4 md:p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">配置包</h3>
              <p className="text-[11px] text-muted-foreground mt-1">一键导入/导出 Agent、关注列表与系统设置</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" className="h-8" onClick={exportTemplate} disabled={exporting}>
                <Download className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">导出</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8"
                onClick={() => importFileRef.current?.click()}
                disabled={importing}
              >
                <Upload className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">导入</span>
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-2 mb-4">
            <div className="text-[11px] text-muted-foreground">导入模式</div>
            <Select value={importMode} onValueChange={(v) => setImportMode(v as any)}>
              <SelectTrigger className="h-8 w-[160px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="merge">合并更新（推荐）</SelectItem>
                <SelectItem value="replace">替换（仅覆盖配置包包含项）</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <input
            ref={importFileRef}
            type="file"
            accept="application/json"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              try {
                const text = await file.text()
                const payload = JSON.parse(text)
                await importTemplate(payload)
              } catch (err) {
                toast('配置包解析失败', 'error')
              }
            }}
          />

          <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-foreground">
              <FileJson className="w-4 h-4 text-muted-foreground" />
              官方模板
            </div>
            <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2">
              {builtinTemplates.map(t => (
                <div key={t.name} className="rounded-lg border border-border/40 bg-background/30 p-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[12px] font-semibold text-foreground">{t.name}</div>
                    <Button
                      size="sm"
                      className="h-7"
                      onClick={() => importTemplate(t.payload)}
                      disabled={importing}
                    >
                      <span className="text-[12px]">应用</span>
                    </Button>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">{t.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Feedback Stats */}
        <section className="card p-4 md:p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">建议反馈</h3>
              <p className="text-[11px] text-muted-foreground mt-1">用于评估推送质量与策略迭代</p>
            </div>
            <Button variant="secondary" size="sm" className="h-8" onClick={loadFeedbackStats} disabled={fbLoading}>
              <BarChart3 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">刷新</span>
            </Button>
          </div>

          {fbStats ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
                <span>近 {fbStats.range_days} 天</span>
                <span className="opacity-50">|</span>
                <span>反馈: <span className="font-mono text-foreground/90">{fbStats.total}</span></span>
                <span className="opacity-50">|</span>
                <span>有用: <span className="font-mono text-emerald-600">{fbStats.useful}</span></span>
                <span className="opacity-50">|</span>
                <span>没用: <span className="font-mono text-rose-600">{fbStats.useless}</span></span>
                <span className="opacity-50">|</span>
                <span>有用率: <span className="font-mono text-foreground/90">{Math.round(fbStats.useful_rate * 100)}%</span></span>
              </div>

              {fbStats.by_agent?.length ? (
                <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
                  <div className="text-[12px] font-semibold text-foreground">按 Agent</div>
                  <div className="mt-2 space-y-1">
                    {fbStats.by_agent.slice(0, 6).map(a => (
                      <div key={a.agent_name} className="flex items-center justify-between text-[11px]">
                        <span className="font-mono text-muted-foreground">{a.agent_name}</span>
                        <span className="font-mono text-muted-foreground">
                          {a.useful}/{a.total} ({Math.round(a.useful_rate * 100)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-[12px] text-muted-foreground">暂无反馈数据</div>
              )}
            </div>
          ) : (
            <div className="text-[12px] text-muted-foreground">暂无反馈数据</div>
          )}
        </section>

        {/* General Settings */}
        {settings.length > 0 && (
          <section className="card p-4 md:p-6">
            <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground mb-4 md:mb-5">系统</h3>
            <div className="space-y-5">
              {settings.map(setting => {
                const currentValue = edited[setting.key] ?? setting.value
                const isChanged = setting.key in edited
                return (
                  <div key={setting.key}>
                    <Label>{setting.description || setting.key}</Label>
                    <div className="flex items-center gap-2.5">
                      <Input
                        value={currentValue}
                        onChange={e => setEdited({ ...edited, [setting.key]: e.target.value })}
                        className={`font-mono ${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}
                        placeholder={setting.key}
                      />
                      <button
                        onClick={() => handleSave(setting.key)}
                        disabled={!isChanged || saving === setting.key}
                        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-all ${
                          saved === setting.key
                            ? 'bg-emerald-500/10 text-emerald-600'
                            : isChanged
                              ? 'bg-primary text-white'
                              : 'text-muted-foreground/30'
                        }`}
                      >
                        {saving === setting.key ? (
                          <span className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <Check className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )}
      </div>

      {/* Service Dialog */}
      <Dialog open={serviceDialogOpen} onOpenChange={setServiceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editServiceId ? '编辑 AI 服务商' : '添加 AI 服务商'}</DialogTitle>
            <DialogDescription>配置 AI 服务商的 API 连接信息</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>名称</Label>
              <Input
                value={serviceForm.name}
                onChange={e => setServiceForm({ ...serviceForm, name: e.target.value })}
                placeholder="如 OpenAI、智谱、DeepSeek"
              />
            </div>
            <div>
              <Label>Base URL</Label>
              <Input
                value={serviceForm.base_url}
                onChange={e => setServiceForm({ ...serviceForm, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="font-mono"
              />
            </div>
            <div>
              <Label>API Key</Label>
              <div className="relative">
                <Input
                  type={serviceKeyVisible ? 'text' : 'password'}
                  value={serviceForm.api_key}
                  onChange={e => setServiceForm({ ...serviceForm, api_key: e.target.value })}
                  placeholder="sk-..."
                  className="font-mono pr-10"
                />
                <Button
                  type="button" variant="ghost" size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setServiceKeyVisible(!serviceKeyVisible)}
                >
                  {serviceKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setServiceDialogOpen(false)}>取消</Button>
              <Button onClick={saveService} disabled={!serviceForm.name || !serviceForm.base_url}>
                {editServiceId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Model Dialog */}
      <Dialog open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editModelId ? '编辑模型' : '添加模型'}</DialogTitle>
            <DialogDescription>配置 AI 模型</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>所属服务商</Label>
              <Select
                value={modelForm.service_id?.toString() ?? ''}
                onValueChange={val => setModelForm({ ...modelForm, service_id: val ? parseInt(val) : null })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择服务商" />
                </SelectTrigger>
                <SelectContent>
                  {services.map(s => (
                    <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>显示名称 <span className="text-muted-foreground font-normal">(选填，默认同模型标识)</span></Label>
              <Input
                value={modelForm.name}
                onChange={e => setModelForm({ ...modelForm, name: e.target.value })}
                placeholder="不填则使用模型标识"
              />
            </div>
            <div>
              <Label>模型标识</Label>
              <Input
                value={modelForm.model}
                onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                placeholder="gpt-4o / glm-4-flash"
                className="font-mono"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setModelDialogOpen(false)}>取消</Button>
              <Button onClick={saveModel} disabled={!modelForm.model || !modelForm.service_id}>
                {editModelId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Channel Dialog */}
      <Dialog open={channelDialogOpen} onOpenChange={setChannelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editChannelId ? '编辑通知渠道' : '添加通知渠道'}</DialogTitle>
            <DialogDescription>配置通知推送方式</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>名称</Label>
              <Input
                value={channelForm.name}
                onChange={e => setChannelForm({ ...channelForm, name: e.target.value })}
                placeholder="如 我的 Telegram"
              />
            </div>
            <div>
              <Label>类型</Label>
              <Select
                value={channelForm.type}
                onValueChange={val => setChannelForm({ ...channelForm, type: val, config: {} })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_FIELDS).map(([key, def]) => (
                    <SelectItem key={key} value={key}>{def.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {CHANNEL_TYPE_FIELDS[channelForm.type]?.fields.map(field => (
              <div key={field.key}>
                <Label>{field.label}{!field.required && <span className="text-muted-foreground font-normal"> (选填)</span>}</Label>
                <div className="relative">
                  <Input
                    type={field.secret && !channelKeyVisible ? 'password' : 'text'}
                    value={channelForm.config[field.key] || ''}
                    onChange={e => setChannelForm({
                      ...channelForm,
                      config: { ...channelForm.config, [field.key]: e.target.value },
                    })}
                    placeholder={field.placeholder}
                    className={`font-mono ${field.secret ? 'pr-10' : ''}`}
                  />
                  {field.secret && (
                    <Button
                      type="button" variant="ghost" size="icon"
                      className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                      onClick={() => setChannelKeyVisible(!channelKeyVisible)}
                    >
                      {channelKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  )}
                </div>
              </div>
            ))}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setChannelDialogOpen(false)}>取消</Button>
              <Button onClick={saveChannel} disabled={!isChannelFormValid()}>
                {editChannelId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Version Footer */}
      {version && (
        <div className="mt-8 text-center text-[11px] text-muted-foreground/60">
          PanWatch v{version}
        </div>
      )}
    </div>
  )
}
