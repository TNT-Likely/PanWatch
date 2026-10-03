import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useState, useEffect, useRef } from 'react'
import { Check, Eye, EyeOff, Plus, Pencil, Trash2, Star, Send, Cpu, Play, Download, Upload, Radar, AlertTriangle, Palette } from 'lucide-react'
import { fetchAPI, type AIService, type AIModel, type NotifyChannel } from '@panwatch/api'
import { useAvatar, saveAvatar, fileToAvatarDataUrl } from '@/hooks/use-avatar'
import { buildTemplateImportFeedback, type TemplateImportSummary } from '@/lib/template-import-feedback'
import PatSection from '@/components/PatSection'
import { UserAvatar } from '@/components/UserAvatar'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import { useMarketColors } from '@/hooks/use-market-colors'
import type { MarketColorPreference } from '@/lib/market-colors'

interface Setting {
  key: string
  value: string
  description: string
}

type TemplateModule = 'settings' | 'ai' | 'notifications' | 'agents' | 'watchlist' | 'portfolio'

interface TemplatePayload {
  version: number
  exported_at?: string
  modules?: TemplateModule[]
  settings?: Record<string, string>
  ai_services?: unknown[]
  notify_channels?: unknown[]
  agents?: any[]
  stocks?: any[]
  accounts?: unknown[]
}

interface TemplateImportResponse {
  modules?: TemplateModule[]
  summary?: TemplateImportSummary
}

const TEMPLATE_MODULES: Array<{
  id: TemplateModule
  labelKey: TemplateModule
  descriptionKey: TemplateModule
  sensitive?: boolean
}> = [
  { id: 'settings', labelKey: 'settings', descriptionKey: 'settings' },
  { id: 'ai', labelKey: 'ai', descriptionKey: 'ai', sensitive: true },
  { id: 'notifications', labelKey: 'notifications', descriptionKey: 'notifications', sensitive: true },
  { id: 'agents', labelKey: 'agents', descriptionKey: 'agents' },
  { id: 'watchlist', labelKey: 'watchlist', descriptionKey: 'watchlist' },
  { id: 'portfolio', labelKey: 'portfolio', descriptionKey: 'portfolio' },
]

const ALL_TEMPLATE_MODULES = TEMPLATE_MODULES.map(item => item.id)

const detectTemplateModules = (payload: TemplatePayload): TemplateModule[] => {
  if (Array.isArray(payload.modules) && payload.modules.length > 0) {
    return ALL_TEMPLATE_MODULES.filter(module => payload.modules?.includes(module))
  }
  const detected: TemplateModule[] = []
  if (Object.prototype.hasOwnProperty.call(payload, 'settings')) detected.push('settings')
  if (Object.prototype.hasOwnProperty.call(payload, 'ai_services')) detected.push('ai')
  if (Object.prototype.hasOwnProperty.call(payload, 'notify_channels')) detected.push('notifications')
  if (Object.prototype.hasOwnProperty.call(payload, 'agents')) detected.push('agents')
  if (Object.prototype.hasOwnProperty.call(payload, 'stocks')) detected.push('watchlist')
  if (Object.prototype.hasOwnProperty.call(payload, 'accounts')) detected.push('portfolio')
  return detected
}


interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
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
  labelKey: string
  placeholderKey: string
  secret?: boolean
  required?: boolean
}

interface ChannelTypeDef { labelKey: string; fields: ChannelFieldDef[] }

const CHANNEL_TYPE_FIELDS: Record<string, ChannelTypeDef> = {
  telegram: {
    labelKey: 'telegram',
    fields: [
      { key: 'bot_token', labelKey: 'botToken', placeholderKey: 'botToken', secret: true, required: true },
      { key: 'chat_id', labelKey: 'chatId', placeholderKey: 'chatId', required: true },
      { key: 'proxy', labelKey: 'proxy', placeholderKey: 'proxy' },
    ],
  },
  bark: {
    labelKey: 'bark',
    fields: [
      { key: 'device_key', labelKey: 'deviceKey', placeholderKey: 'deviceKey', required: true },
      { key: 'server_url', labelKey: 'serverUrl', placeholderKey: 'serverUrl' },
    ],
  },
  dingtalk: {
    labelKey: 'dingtalk',
    fields: [
      { key: 'token', labelKey: 'webhookToken', placeholderKey: 'accessToken', secret: true, required: true },
      { key: 'secret', labelKey: 'signSecret', placeholderKey: 'signSecret', secret: true },
      { key: 'phones', labelKey: 'phones', placeholderKey: 'phones' },
      { key: 'keyword', labelKey: 'keyword', placeholderKey: 'keyword' },
    ],
  },
  wecom: {
    labelKey: 'wecom',
    fields: [
      { key: 'webhook_key', labelKey: 'webhookKey', placeholderKey: 'webhookKey', secret: true, required: true },
    ],
  },
  lark: {
    labelKey: 'lark',
    fields: [
      { key: 'webhook_token', labelKey: 'webhookToken', placeholderKey: 'webhookToken', secret: true, required: true },
    ],
  },
  serverchan: {
    labelKey: 'serverchan',
    fields: [
      { key: 'sendkey', labelKey: 'sendKey', placeholderKey: 'sendKey', secret: true, required: true },
    ],
  },
  pushplus: {
    labelKey: 'pushplus',
    fields: [
      { key: 'token', labelKey: 'token', placeholderKey: 'pushplusToken', secret: true, required: true },
      { key: 'topic', labelKey: 'groupCode', placeholderKey: 'groupCode' },
    ],
  },
  discord: {
    labelKey: 'discord',
    fields: [
      { key: 'webhook_id', labelKey: 'webhookId', placeholderKey: 'webhookId', required: true },
      { key: 'webhook_token', labelKey: 'webhookToken', placeholderKey: 'webhookToken', secret: true, required: true },
    ],
  },
  pushover: {
    labelKey: 'pushover',
    fields: [
      { key: 'user_key', labelKey: 'userKey', placeholderKey: 'userKey', required: true },
      { key: 'app_token', labelKey: 'appToken', placeholderKey: 'appToken', secret: true, required: true },
    ],
  },
}

const emptyServiceForm: ServiceForm = { name: '', base_url: '', api_key: '' }
const emptyModelForm: ModelForm = { name: '', service_id: null, model: '' }
const emptyChannelForm: ChannelForm = { name: '', type: 'telegram', config: {} }

export default function SettingsPage() {
  const { t } = useTranslation(['configuration', 'common'])
  const confirmAction = useConfirm()
  const configT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const { preference: marketColorPreference, setPreference: setMarketColorPreference } = useMarketColors()
  const [settings, setSettings] = useState<Setting[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [version, setVersion] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  const [systemQuery, setSystemQuery] = useState('')

  // Service dialog
  const [serviceDialogOpen, setServiceDialogOpen] = useState(false)
  const [serviceForm, setServiceForm] = useState<ServiceForm>(emptyServiceForm)
  const [editServiceId, setEditServiceId] = useState<number | null>(null)
  const [serviceKeyVisible, setServiceKeyVisible] = useState(false)

  // Model dialog
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm)
  const [editModelId, setEditModelId] = useState<number | null>(null)

  // 批量选择嗅探到的模型
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchServiceId, setBatchServiceId] = useState<number | null>(null)
  const [batchCandidates, setBatchCandidates] = useState<string[]>([])
  const [batchChecked, setBatchChecked] = useState<Set<string>>(new Set())
  const [batchDefault, setBatchDefault] = useState<string>('')
  const [submittingBatch, setSubmittingBatch] = useState(false)
  const [discoveringService, setDiscoveringService] = useState<number | null>(null)

  // Channel dialog
  const [channelDialogOpen, setChannelDialogOpen] = useState(false)
  const [channelForm, setChannelForm] = useState<ChannelForm>(emptyChannelForm)
  const [editChannelId, setEditChannelId] = useState<number | null>(null)
  const [channelKeyVisible, setChannelKeyVisible] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [testingModel, setTestingModel] = useState<number | null>(null)

  // 头像
  const avatar = useAvatar()
  const avatarFileRef = useRef<HTMLInputElement | null>(null)
  const [avatarSaving, setAvatarSaving] = useState(false)

  // Templates (config pack)
  const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [exportModules, setExportModules] = useState<TemplateModule[]>(ALL_TEMPLATE_MODULES)
  const [importModules, setImportModules] = useState<TemplateModule[]>([])
  const [availableImportModules, setAvailableImportModules] = useState<TemplateModule[]>([])
  const [pendingImport, setPendingImport] = useState<TemplatePayload | null>(null)

  const importFileRef = useRef<HTMLInputElement | null>(null)

  const { toast } = useToast()

  const loadGeneration = useRef(0)
  const load = async () => {
    const generation = ++loadGeneration.current
    const current = () => generation === loadGeneration.current
    const publish = <T,>(setter: (value: T) => void) => (value: T) => { if (current()) setter(value) }
    const results = await Promise.allSettled([
      fetchAPI<Setting[]>('/settings').then(publish(setSettings)).finally(() => { if (current()) setLoading(false) }),
      fetchAPI<AIService[]>('/providers/services').then(publish(setServices)),
      fetchAPI<NotifyChannel[]>('/channels').then(publish(setChannels)),
      fetchAPI<{ version: string }>('/settings/version').then(publish(data => setVersion(data.version))),
      fetchAPI<AgentsHealth>('/agents/health').then(publish(setHealth)),
    ])
    if (current()) results.forEach(result => { if (result.status === 'rejected') console.error(result.reason) })
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
    if (exportModules.length === 0) return
    setExporting(true)
    try {
      const moduleQuery = encodeURIComponent(exportModules.join(','))
      const data = await fetchAPI<TemplatePayload>(`/templates/export?modules=${moduleQuery}`)
      const date = new Date().toISOString().slice(0, 10)
      downloadJson(`panwatch-config-${date}.json`, data)
      toast(configT('configuration:settingsPage.messages.exportSuccess', { count: exportModules.length }), 'success')
      setExportDialogOpen(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.exportFailed'), 'error')
    } finally {
      setExporting(false)
    }
  }

  const importTemplate = async (payload: TemplatePayload, selectedModules: TemplateModule[]) => {
    if (selectedModules.length === 0) return null
    setImporting(true)
    try {
      const moduleQuery = encodeURIComponent(selectedModules.join(','))
      const resp = await fetchAPI<TemplateImportResponse>(`/templates/import?mode=${importMode}&modules=${moduleQuery}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      const feedback = buildTemplateImportFeedback(resp.summary, configT)
      toast(feedback.successMessage, 'success')
      if (feedback.warningMessage) toast(feedback.warningMessage, 'info')
      setImportDialogOpen(false)
      setPendingImport(null)
      // refresh
      await load()
      return resp
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.importFailed'), 'error')
      return null
    } finally {
      setImporting(false)
    }
  }

  const toggleTemplateModule = (
    module: TemplateModule,
    selected: TemplateModule[],
    setSelected: (modules: TemplateModule[]) => void,
  ) => {
    setSelected(
      selected.includes(module)
        ? selected.filter(item => item !== module)
        : ALL_TEMPLATE_MODULES.filter(item => item === module || selected.includes(item)),
    )
  }

  const prepareTemplateImport = (payload: TemplatePayload) => {
    const available = detectTemplateModules(payload)
    if (available.length === 0) {
      toast(configT('configuration:settingsPage.messages.noModules'), 'error')
      return
    }
    setPendingImport(payload)
    setAvailableImportModules(available)
    setImportModules(available)
    setImportDialogOpen(true)
  }

  useEffect(() => { void load(); return () => { loadGeneration.current++ } }, [])

  const onPickAvatar = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = '' // 允许重复选择同一文件
    if (!file) return
    setAvatarSaving(true)
    try {
      const dataUrl = await fileToAvatarDataUrl(file)
      await saveAvatar(dataUrl)
      toast(configT('configuration:settingsPage.messages.avatarUpdated'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : configT('configuration:settingsPage.messages.avatarSaveFailed'), 'error')
    } finally {
      setAvatarSaving(false)
    }
  }


  const handleSave = async (key: string) => {
    setSaving(key)
    try {
      const value = edited[key] ?? settings.find(s => s.key === key)?.value
      await fetchAPI(`/settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value }),
      })
      const newEdited = { ...edited }
      delete newEdited[key]
      setEdited(newEdited)
      setSaved(key)
      setTimeout(() => setSaved(null), 2000)
      load()
    } catch {
      toast(configT('configuration:settingsPage.messages.saveFailed'), 'error')
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
      let serviceId = editServiceId
      if (editServiceId) {
        await fetchAPI(`/providers/services/${editServiceId}`, { method: 'PUT', body: JSON.stringify(serviceForm) })
      } else {
        const created = await fetchAPI<AIService>('/providers/services', { method: 'POST', body: JSON.stringify(serviceForm) })
        serviceId = created.id
      }
      setServiceDialogOpen(false)
      await load()
      if (!editServiceId && serviceId) {
        try {
          const res = await fetchAPI<{ models: string[] }>(
            `/providers/services/${serviceId}/discover-models`,
            { method: 'POST' },
          )
          const found = res.models.filter(Boolean)
          if (found.length > 0) {
            setBatchServiceId(serviceId)
            setBatchCandidates(found)
            setBatchChecked(new Set())
            setBatchDefault('')
            setBatchOpen(true)
          } else {
            toast(configT('configuration:settingsPage.messages.serviceSavedNoModels'), 'info')
          }
        } catch (e) {
          toast(
            e instanceof Error
              ? configT('configuration:settingsPage.messages.serviceDiscoverFailed', { message: e.message })
              : configT('configuration:settingsPage.messages.serviceNoDiscover'),
            'info',
          )
        }
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
    }
  }

  // 手动对某服务商嗅探并打开批量选择框(排除已添加的模型)
  const discoverForService = async (serviceId: number) => {
    setDiscoveringService(serviceId)
    try {
      const res = await fetchAPI<{ models: string[] }>(
        `/providers/services/${serviceId}/discover-models`,
        { method: 'POST' },
      )
      const svc = services.find(s => s.id === serviceId)
      const added = new Set((svc?.models || []).map(m => m.model))
      const found = res.models.filter(Boolean).filter(id => !added.has(id))
      if (found.length === 0) {
        toast(configT('configuration:settingsPage.messages.noNewModels'), 'info')
        return
      }
      setBatchServiceId(serviceId)
      setBatchCandidates(found)
      setBatchChecked(new Set())
      setBatchDefault('')
      setBatchOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.discoverUnsupported'), 'error')
    } finally {
      setDiscoveringService(null)
    }
  }

  const submitBatchModels = async () => {
    if (!batchServiceId) return
    const models = Array.from(batchChecked).map(m => ({
      name: '',
      model: m,
      is_default: m === batchDefault,
    }))
    if (models.length === 0) { setBatchOpen(false); return }
    setSubmittingBatch(true)
    try {
      await fetchAPI(`/providers/services/${batchServiceId}/models/batch`, {
        method: 'POST',
        body: JSON.stringify({ models }),
      })
      setBatchOpen(false)
      toast(configT('configuration:settingsPage.messages.modelsAdded', { count: models.length }), 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.batchAddFailed'), 'error')
    } finally {
      setSubmittingBatch(false)
    }
  }

  const deleteService = async (id: number) => {
    if (!(await confirmAction(configT('configuration:settingsPage.messages.deleteServiceConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/providers/services/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
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
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
    }
  }

  const deleteModel = async (id: number) => {
    if (!(await confirmAction(configT('configuration:settingsPage.messages.deleteModelConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
    }
  }

  const setDefaultModel = async (id: number) => {
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast(configT('configuration:settingsPage.messages.settingFailed'), 'error')
    }
  }

  const testModel = async (id: number) => {
    setTestingModel(id)
    try {
      await fetchAPI(`/providers/models/${id}/test`, { method: 'POST' })
      toast(configT('configuration:settingsPage.messages.modelTested'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.testFailed'), 'error')
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
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
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
    if (!(await confirmAction(configT('configuration:settingsPage.messages.deleteChannelConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/channels/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.saveFailed'), 'error')
    }
  }

  const setDefaultChannel = async (id: number) => {
    try {
      await fetchAPI(`/channels/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast(configT('configuration:settingsPage.messages.settingFailed'), 'error')
    }
  }

  const toggleChannelEnabled = async (channel: NotifyChannel) => {
    try {
      await fetchAPI(`/channels/${channel.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !channel.enabled }) })
      load()
    } catch {
      toast(configT('configuration:settingsPage.messages.operationFailed'), 'error')
    }
  }

  const testChannel = async (id: number) => {
    setTesting(id)
    try {
      await fetchAPI(`/channels/${id}/test`, { method: 'POST' })
      toast(configT('configuration:settingsPage.messages.notificationSent'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : configT('configuration:settingsPage.messages.testFailed'), 'error')
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

  const allModels = services.flatMap(s => s.models || [])
  const defaultModel = allModels.find(m => m.is_default)
  const defaultChannel = channels.find(c => c.is_default)
  const enabledChannels = channels.filter(c => c.enabled)

  const settingLabel = (setting: Setting): string => {
    const key = `configuration:settingsPage.system.settingDescriptions.${setting.key}`
    const translated = configT(key)
    return translated === key ? (setting.description || setting.key) : translated
  }

  const filteredSettings = settings.filter(s => {
    const q = systemQuery.trim().toLowerCase()
    if (!q) return true
    return settingLabel(s).toLowerCase().includes(q) || (s.key || '').toLowerCase().includes(q)
  })

  // 按“重要性”排序：常用优先，低频靠后
  const jumpItems: Array<{ id: string; label: string; hint?: string }> = [
    { id: 'sec-ai', label: configT('configuration:settingsPage.nav.ai'), hint: `${services.length} ${configT('configuration:settingsPage.hero.providers')} / ${allModels.length} ${configT('configuration:settingsPage.hero.models')}` },
    { id: 'sec-notify', label: configT('configuration:settingsPage.nav.notifications'), hint: `${enabledChannels.length}/${channels.length} ${configT('configuration:settingsPage.hero.channelsEnabled')}` },
    { id: 'sec-system', label: configT('configuration:settingsPage.nav.system'), hint: health?.timezone ? `TZ ${health.timezone}` : undefined },
    { id: 'sec-appearance', label: configT('configuration:settingsPage.nav.appearance') },
    { id: 'sec-pat', label: configT('configuration:settingsPage.nav.pat') },
  ]

  const scrollTo = (id: string) => {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div>
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
            const payload = JSON.parse(text) as TemplatePayload
            prepareTemplateImport(payload)
          } catch {
            toast(configT('configuration:settingsPage.messages.configParseFailed'), 'error')
          }
        }}
      />

      {/* Hero */}
      <div className="card relative overflow-hidden p-5 md:p-7">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-accent/30" />
        <div className="relative flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <input ref={avatarFileRef} type="file" accept="image/*" className="hidden" onChange={onPickAvatar} />
              <button
                type="button"
                onClick={() => avatarFileRef.current?.click()}
                disabled={avatarSaving}
                title={configT('configuration:settingsPage.hero.uploadAvatar')}
                className="group relative h-9 w-9 rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 text-white shadow-sm flex items-center justify-center ring-1 ring-border/40 hover:ring-primary/40 transition-all shrink-0"
              >
                <UserAvatar src={avatar} alt={configT('configuration:settingsPage.hero.avatarAlt')} />
                <span className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                  <Upload className="w-3.5 h-3.5 text-white" />
                </span>
              </button>
              <span className="mx-1 hidden h-4 w-px bg-border/50 sm:block" />
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{services.length}</span> {configT('configuration:settingsPage.hero.providers')}
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{allModels.length}</span> {configT('configuration:settingsPage.hero.models')}
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{enabledChannels.length}</span>/<span className="font-mono">{channels.length}</span> {configT('configuration:settingsPage.hero.channelsEnabled')}
              </div>
              {defaultModel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  {configT('configuration:settingsPage.hero.defaultModel')} <span className="font-mono text-foreground/90">{defaultModel.model}</span>
                </div>
              ) : null}
              {defaultChannel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  {configT('configuration:settingsPage.hero.defaultNotification')} <span className="text-foreground/90">{defaultChannel.name}</span>
                </div>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <Button
              variant="secondary"
              size="sm"
              className="h-9"
              onClick={() => importFileRef.current?.click()}
              disabled={importing}
            >
              <Upload className="w-3.5 h-3.5" /> {importing ? configT('configuration:settingsPage.hero.importing') : configT('configuration:settingsPage.hero.importPack')}
            </Button>
            <Button variant="secondary" size="sm" className="h-9" onClick={() => setExportDialogOpen(true)} disabled={exporting}>
              <Download className="w-3.5 h-3.5" /> {configT('configuration:settingsPage.hero.exportPack')}
            </Button>
            <Button size="sm" className="h-9" onClick={() => scrollTo('sec-ai')}>
              <Cpu className="w-3.5 h-3.5" /> {configT('configuration:settingsPage.hero.configureAi')}
            </Button>
          </div>
        </div>

        {/* Jump pills */}
        <div className="relative mt-4 flex flex-wrap gap-2">
          {jumpItems.map(it => (
            <button
              key={it.id}
              onClick={() => scrollTo(it.id)}
              className="group flex items-center gap-2 rounded-full border border-border/50 bg-background/70 px-3 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:border-primary/30 transition-colors"
            >
              <span className="font-medium text-foreground/90 group-hover:text-foreground">{it.label}</span>
              {it.hint ? <span className="opacity-60">{it.hint}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* AI Services + Models Section */}
        <section id="sec-ai" className="card p-4 md:p-6 lg:col-span-7">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">{configT('configuration:settingsPage.ai.title')}</h3>
              <p className="text-[11px] text-muted-foreground mt-1">{configT('configuration:settingsPage.ai.description')}</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openServiceDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{configT('configuration:settingsPage.ai.addProvider')}</span>
            </Button>
          </div>
          {services.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">{configT('configuration:settingsPage.ai.empty')}</p>
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
                        <Plus className="w-3 h-3" /> {configT('configuration:settingsPage.ai.addModel')}
                      </Button>
                      <Button
                        variant="ghost" size="icon" className="h-7 w-7"
                        title={configT('configuration:settingsPage.ai.discover')}
                        disabled={discoveringService === svc.id}
                        onClick={() => discoverForService(svc.id)}
                      >
                        <Radar className={`w-3.5 h-3.5 ${discoveringService === svc.id ? 'animate-pulse' : ''}`} />
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
                              title={configT('configuration:settingsPage.ai.test')}
                            >
                              {testingModel === m.id ? (
                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                              ) : (
                                <Play className="w-3 h-3" />
                              )}
                            </Button>
                            {!m.is_default && (
                              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setDefaultModel(m.id)} title={configT('configuration:settingsPage.ai.setDefault')}>
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
        <section id="sec-notify" className="card p-4 md:p-6 lg:col-span-5">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">{configT('configuration:settingsPage.notifications.title')}</h3>
              <p className="text-[11px] text-muted-foreground mt-1">{configT('configuration:settingsPage.notifications.description')}</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openChannelDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{configT('configuration:settingsPage.notifications.add')}</span>
            </Button>
          </div>
          {channels.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">{configT('configuration:settingsPage.notifications.empty')}</p>
          ) : (
            <div className="space-y-3">
              {channels.map(ch => (
                <div key={ch.id} className="flex items-center justify-between p-3.5 rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    {ch.is_default && <Star className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" />}
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{ch.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{configT(`configuration:settingsPage.channels.types.${CHANNEL_TYPE_FIELDS[ch.type]?.labelKey || ch.type}`)}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => testChannel(ch.id)}
                      disabled={testing === ch.id || !ch.enabled}
                      title={configT('configuration:settingsPage.notifications.sendTest')}
                    >
                      {testing === ch.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Send className="w-3.5 h-3.5" />
                      )}
                    </Button>
                    {!ch.is_default && (
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDefaultChannel(ch.id)} title={configT('configuration:settingsPage.notifications.setDefault')}>
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

        {/* General Settings */}
        {settings.length > 0 && (
          <section id="sec-system" className="card p-4 md:p-6 lg:col-span-12">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-4 md:mb-5">
              <div>
                <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">{configT('configuration:settingsPage.system.title')}</h3>
                <p className="text-[11px] text-muted-foreground mt-1">{configT('configuration:settingsPage.system.description')}</p>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={systemQuery}
                  onChange={e => setSystemQuery(e.target.value)}
                  placeholder={configT('configuration:settingsPage.system.searchPlaceholder')}
                  className="h-9 w-full md:w-[320px]"
                />
                {health?.timezone ? (
                  <div className="hidden md:flex px-2.5 h-9 items-center rounded-lg border border-border/50 bg-accent/20 text-[11px] text-muted-foreground">
                    TZ <span className="ml-1 font-mono text-foreground/90">{health.timezone}</span>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="space-y-5">
              {filteredSettings.map(setting => {
                const currentValue = edited[setting.key] ?? setting.value
                const isChanged = setting.key in edited
                const STOCK_LINK_OPTIONS: Record<string, string> = { xueqiu: configT('configuration:settingsPage.system.stockLinkXueqiu') }
                const label = settingLabel(setting)
                return (
                  <div key={setting.key}>
                    <Label>{label}</Label>
                    <div className="flex items-center gap-2.5">
                      {setting.key === 'stock_link_platform' ? (
                        <Select
                          value={currentValue || 'xueqiu'}
                          onValueChange={v => setEdited({ ...edited, [setting.key]: v })}
                        >
                          <SelectTrigger className={`${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(STOCK_LINK_OPTIONS).map(([val, label]) => (
                              <SelectItem key={val} value={val}>{label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                      <Input
                        value={currentValue}
                        onChange={e => setEdited({ ...edited, [setting.key]: e.target.value })}
                        className={`font-mono ${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}
                        placeholder={setting.key}
                      />
                      )}
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

        {/* Display preferences */}
        <section id="sec-appearance" className="card p-4 md:p-5 lg:col-span-5">
          <div className="flex items-start gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Palette className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0">
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">{configT('configuration:settingsPage.appearance.title')}</h3>
              <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">{configT('configuration:settingsPage.appearance.description')}</p>
            </div>
          </div>
          <div className="mt-3 space-y-1.5" role="radiogroup" aria-label={configT('configuration:settingsPage.appearance.marketColors')}>
            {(['auto', 'red-up', 'green-up'] as MarketColorPreference[]).map(option => {
              const active = marketColorPreference === option
              return (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setMarketColorPreference(option)}
                  className={`flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors ${active ? 'border-primary/40 bg-primary/5' : 'border-border/50 hover:border-primary/25 hover:bg-accent/20'}`}
                >
                  <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${active ? 'border-primary bg-primary text-primary-foreground' : 'border-border'}`}>
                    {active ? <Check className="h-2.5 w-2.5" /> : null}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[11px] font-medium text-foreground">{configT(`configuration:settingsPage.appearance.options.${option}.label`)}</span>
                    <span className="block truncate text-[9px] text-muted-foreground">{configT(`configuration:settingsPage.appearance.options.${option}.description`)}</span>
                  </span>
                  <span className="flex shrink-0 gap-1.5 font-mono text-[10px]">
                    <span className={option === 'green-up' ? 'text-green-600 dark:text-green-400' : option === 'red-up' ? 'text-rose-600 dark:text-rose-400' : 'text-market-up'}>+2.35%</span>
                    <span className={option === 'green-up' ? 'text-rose-600 dark:text-rose-400' : option === 'red-up' ? 'text-green-600 dark:text-green-400' : 'text-market-down'}>-1.18%</span>
                  </span>
                </button>
              )
            })}
          </div>
        </section>

        {/* MCP 访问令牌 */}
        <PatSection className="lg:col-span-7" />

      </div>

      {/* Export module picker */}
      <Dialog open={exportDialogOpen} onOpenChange={setExportDialogOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{configT('configuration:settingsPage.pack.chooseExport')}</DialogTitle>
            <DialogDescription>{configT('configuration:settingsPage.pack.chooseExportDescription')}</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
            {TEMPLATE_MODULES.map(module => (
              <label
                key={module.id}
                className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${
                  exportModules.includes(module.id)
                    ? 'border-primary/40 bg-primary/5'
                    : 'border-border/50 bg-accent/20 hover:border-primary/20'
                }`}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-primary"
                  checked={exportModules.includes(module.id)}
                  onChange={() => toggleTemplateModule(module.id, exportModules, setExportModules)}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
                    {configT(`configuration:settingsPage.modules.${module.labelKey}.label`)}
                    {module.sensitive ? <span className="text-[10px] text-amber-600">{configT('configuration:settingsPage.pack.sensitive')}</span> : null}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-muted-foreground">{configT(`configuration:settingsPage.modules.${module.descriptionKey}.description`)}</span>
                </span>
              </label>
            ))}
          </div>
          {exportModules.some(module => module === 'ai' || module === 'notifications') ? (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-[11px] text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {configT('configuration:settingsPage.pack.credentialsWarning')}
            </div>
          ) : null}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setExportDialogOpen(false)}>{configT('configuration:settingsPage.pack.cancel')}</Button>
            <Button onClick={exportTemplate} disabled={exportModules.length === 0 || exporting}>
              <Download className="h-4 w-4" /> {exporting ? configT('configuration:settingsPage.pack.exporting') : configT('configuration:settingsPage.pack.exportCount', { count: exportModules.length })}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Import module picker */}
      <Dialog
        open={importDialogOpen}
        onOpenChange={(open) => {
          setImportDialogOpen(open)
          if (!open) setPendingImport(null)
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{configT('configuration:settingsPage.pack.chooseImport')}</DialogTitle>
            <DialogDescription>
              {configT('configuration:settingsPage.pack.versionDetected', { version: pendingImport?.version || 1, count: availableImportModules.length })}
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
            {TEMPLATE_MODULES.filter(module => availableImportModules.includes(module.id)).map(module => (
              <label
                key={module.id}
                className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${
                  importModules.includes(module.id)
                    ? 'border-primary/40 bg-primary/5'
                    : 'border-border/50 bg-accent/20 hover:border-primary/20'
                }`}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-primary"
                  checked={importModules.includes(module.id)}
                  onChange={() => toggleTemplateModule(module.id, importModules, setImportModules)}
                />
                <span className="min-w-0">
                  <span className="text-[13px] font-medium text-foreground">{configT(`configuration:settingsPage.modules.${module.labelKey}.label`)}</span>
                  <span className="mt-0.5 block text-[11px] text-muted-foreground">{configT(`configuration:settingsPage.modules.${module.descriptionKey}.description`)}</span>
                </span>
              </label>
            ))}
          </div>
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border/50 bg-accent/20 p-3">
            <div>
              <div className="text-[12px] font-medium text-foreground">{configT('configuration:settingsPage.pack.importMode')}</div>
              <div className="text-[10px] text-muted-foreground">{configT('configuration:settingsPage.pack.replaceHint')}</div>
            </div>
            <Select value={importMode} onValueChange={(value) => setImportMode(value as 'merge' | 'replace')}>
              <SelectTrigger className="h-8 w-[150px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="merge">{configT('configuration:settingsPage.pack.merge')}</SelectItem>
                <SelectItem value="replace">{configT('configuration:settingsPage.pack.replaceContained')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setImportDialogOpen(false)}>{configT('configuration:settingsPage.pack.cancel')}</Button>
            <Button
              onClick={() => pendingImport && importTemplate(pendingImport, importModules)}
              disabled={!pendingImport || importModules.length === 0 || importing}
            >
              <Upload className="h-4 w-4" /> {importing ? configT('configuration:settingsPage.pack.importing') : configT('configuration:settingsPage.pack.importCount', { count: importModules.length })}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Service Dialog */}
      <Dialog open={serviceDialogOpen} onOpenChange={setServiceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editServiceId ? configT('configuration:settingsPage.dialogs.providerEdit') : configT('configuration:settingsPage.dialogs.providerAdd')}</DialogTitle>
            <DialogDescription>{configT('configuration:settingsPage.dialogs.providerDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.name')}</Label>
              <Input
                value={serviceForm.name}
                onChange={e => setServiceForm({ ...serviceForm, name: e.target.value })}
                placeholder={configT('configuration:settingsPage.dialogs.providerPlaceholder')}
              />
            </div>
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.baseUrl')}</Label>
              <Input
                value={serviceForm.base_url}
                onChange={e => setServiceForm({ ...serviceForm, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="font-mono"
              />
            </div>
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.apiKey')}</Label>
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
              <Button variant="ghost" onClick={() => setServiceDialogOpen(false)}>{configT('configuration:settingsPage.dialogs.cancel')}</Button>
              <Button onClick={saveService} disabled={!serviceForm.name || !serviceForm.base_url}>
                {editServiceId ? configT('configuration:settingsPage.dialogs.save') : configT('configuration:settingsPage.dialogs.create')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Model Dialog */}
      <Dialog open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editModelId ? configT('configuration:settingsPage.dialogs.modelEdit') : configT('configuration:settingsPage.dialogs.modelAdd')}</DialogTitle>
            <DialogDescription>{configT('configuration:settingsPage.dialogs.modelDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.provider')}</Label>
              <Select
                value={modelForm.service_id?.toString() ?? ''}
                onValueChange={val => setModelForm({ ...modelForm, service_id: val ? parseInt(val) : null })}
              >
                <SelectTrigger>
                  <SelectValue placeholder={configT('configuration:settingsPage.dialogs.providerSelect')} />
                </SelectTrigger>
                <SelectContent>
                  {services.map(s => (
                    <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.displayName')} <span className="text-muted-foreground font-normal">{configT('configuration:settingsPage.dialogs.optionalDefault')}</span></Label>
              <Input
                value={modelForm.name}
                onChange={e => setModelForm({ ...modelForm, name: e.target.value })}
                placeholder={configT('configuration:settingsPage.dialogs.modelNamePlaceholder')}
              />
            </div>
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.modelIdentifier')} <span className="text-muted-foreground font-normal">{configT('configuration:settingsPage.dialogs.discoverHint')}</span></Label>
              <Input
                value={modelForm.model}
                disabled={!modelForm.service_id}
                onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                placeholder={modelForm.service_id ? 'gpt-4o / glm-4-flash' : configT('configuration:settingsPage.dialogs.modelPlaceholder')}
                className="font-mono"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setModelDialogOpen(false)}>{configT('configuration:settingsPage.dialogs.cancel')}</Button>
              <Button onClick={saveModel} disabled={!modelForm.model || !modelForm.service_id}>
                {editModelId ? configT('configuration:settingsPage.dialogs.save') : configT('configuration:settingsPage.dialogs.create')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 批量选择嗅探到的模型 */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{configT('configuration:settingsPage.dialogs.discovered', { count: batchCandidates.length })}</DialogTitle>
            <DialogDescription>{configT('configuration:settingsPage.dialogs.discoveredDescription')}</DialogDescription>
          </DialogHeader>
          <div className="mt-3 flex items-center justify-between px-0.5 text-xs text-muted-foreground">
            <span>{configT('configuration:settingsPage.dialogs.selected')} <span className="font-mono text-foreground">{batchChecked.size}</span> / {batchCandidates.length}</span>
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => setBatchChecked(
                batchChecked.size === batchCandidates.length ? new Set() : new Set(batchCandidates),
              )}
            >
              {batchChecked.size === batchCandidates.length ? configT('configuration:settingsPage.dialogs.deselectAll') : configT('configuration:settingsPage.dialogs.selectAll')}
            </button>
          </div>
          <div className="mt-1.5 max-h-80 space-y-1.5 overflow-y-auto scrollbar pr-1">
            {batchCandidates.map(id => {
              const checked = batchChecked.has(id)
              const isDefault = batchDefault === id
              return (
                <div
                  key={id}
                  onClick={() => {
                    const next = new Set(batchChecked)
                    if (checked) { next.delete(id); if (isDefault) setBatchDefault('') }
                    else next.add(id)
                    setBatchChecked(next)
                  }}
                  className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                    checked ? 'border-primary/60 bg-primary/10' : 'border-border/50 hover:border-border hover:bg-muted/40'
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                      checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'
                    }`}>
                      {checked && <Check className="h-3 w-3" strokeWidth={3} />}
                    </span>
                    <span className="truncate font-mono text-sm">{id}</span>
                  </div>
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      if (isDefault) { setBatchDefault('') }
                      else {
                        setBatchDefault(id)
                        if (!checked) { const next = new Set(batchChecked); next.add(id); setBatchChecked(next) }
                      }
                    }}
                    className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-colors ${
                      isDefault ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Star className={`h-3 w-3 ${isDefault ? 'fill-current' : ''}`} />
                    {isDefault ? configT('configuration:settingsPage.dialogs.default') : configT('configuration:settingsPage.dialogs.setDefault')}
                  </button>
                </div>
              )
            })}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setBatchOpen(false)}>{configT('configuration:settingsPage.dialogs.skip')}</Button>
            <Button onClick={submitBatchModels} disabled={batchChecked.size === 0 || submittingBatch}>
              {submittingBatch ? configT('configuration:settingsPage.dialogs.adding') : configT('configuration:settingsPage.dialogs.addCount', { count: batchChecked.size })}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Channel Dialog */}
      <Dialog open={channelDialogOpen} onOpenChange={setChannelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editChannelId ? configT('configuration:settingsPage.dialogs.channelEdit') : configT('configuration:settingsPage.dialogs.channelAdd')}</DialogTitle>
            <DialogDescription>{configT('configuration:settingsPage.dialogs.channelDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.channelName')}</Label>
              <Input
                value={channelForm.name}
                onChange={e => setChannelForm({ ...channelForm, name: e.target.value })}
                placeholder={configT('configuration:settingsPage.channels.channelNamePlaceholder')}
              />
            </div>
            <div>
              <Label>{configT('configuration:settingsPage.dialogs.channelType')}</Label>
              <Select
                value={channelForm.type}
                onValueChange={val => setChannelForm({ ...channelForm, type: val, config: {} })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_FIELDS).map(([key, def]) => (
                    <SelectItem key={key} value={key}>{configT(`configuration:settingsPage.channels.types.${def.labelKey}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {CHANNEL_TYPE_FIELDS[channelForm.type]?.fields.map(field => (
              <div key={field.key}>
                <Label>{configT(`configuration:settingsPage.channels.fields.${field.labelKey}`)}{!field.required && <span className="text-muted-foreground font-normal"> ({configT('configuration:settingsPage.dialogs.optional')})</span>}</Label>
                <div className="relative">
                  <Input
                    type={field.secret && !channelKeyVisible ? 'password' : 'text'}
                    value={channelForm.config[field.key] || ''}
                    onChange={e => setChannelForm({
                      ...channelForm,
                      config: { ...channelForm.config, [field.key]: e.target.value },
                    })}
                    placeholder={configT(`configuration:settingsPage.channels.placeholders.${field.placeholderKey}`)}
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
              <Button variant="ghost" onClick={() => setChannelDialogOpen(false)}>{configT('configuration:settingsPage.dialogs.cancel')}</Button>
              <Button onClick={saveChannel} disabled={!isChannelFormValid()}>
                {editChannelId ? configT('configuration:settingsPage.dialogs.save') : configT('configuration:settingsPage.dialogs.create')}
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
