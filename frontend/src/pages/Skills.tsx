import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Sparkles, Play, Check, AlertCircle } from 'lucide-react'
import {
  localSkillsApi,
  type LocalSkillItem,
  type HermesStatusResponse,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export default function SkillsPage() {
  const { toast } = useToast()
  const [skills, setSkills] = useState<LocalSkillItem[]>([])
  const [hermes, setHermes] = useState<HermesStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true)
    else setLoading(true)
    try {
      const [list, status] = await Promise.all([
        localSkillsApi.list({ refresh }),
        localSkillsApi.hermesStatus(),
      ])
      setSkills(list || [])
      setHermes(status)
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [toast])

  useEffect(() => {
    void load(true)
  }, [load])

  const toggleEnabled = async (skill: LocalSkillItem) => {
    setToggling(skill.slug)
    try {
      const updated = await localSkillsApi.update(skill.slug, {
        enabled: !skill.enabled,
      })
      setSkills(prev => prev.map(s => (s.slug === skill.slug ? updated : s)))
      toast(updated.enabled ? `已启用 ${updated.display_name}` : `已停用 ${updated.display_name}`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setToggling(null)
    }
  }

  const testHermes = async () => {
    setTesting(true)
    try {
      const firstEnabled = skills.find(s => s.enabled)
      const resp = await localSkillsApi.testHermes(firstEnabled?.slug)
      if (resp.ok) {
        toast(resp.message || 'Hermes 连接正常', 'success')
      } else {
        toast(resp.message || 'Hermes 测试失败', 'error')
      }
      await load(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTesting(false)
    }
  }

  const enabledCount = skills.filter(s => s.enabled).length

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-4 md:space-y-6">
      <div className="card p-5 md:p-6">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-violet-500/70 flex items-center justify-center shadow-sm">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg md:text-xl font-bold">Skill 广场</h1>
              <p className="text-[12px] md:text-[13px] text-muted-foreground mt-1">
                扫描本地 skill，启用后可在股票报告里选择并通过 Hermes 生成分析
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              className="h-9"
              disabled={refreshing}
              onClick={() => void load(true)}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              刷新本地
            </Button>
            <Button size="sm" className="h-9" disabled={testing} onClick={() => void testHermes()}>
              {testing ? (
                <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5" />
              )}
              测试 Hermes
            </Button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 text-[11px]">
          <Badge variant="secondary" className="font-normal">
            已发现 {skills.length} 个 skill
          </Badge>
          <Badge variant="secondary" className="font-normal">
            已启用 {enabledCount}
          </Badge>
          {hermes ? (
            <Badge
              variant="secondary"
              className={`font-normal ${hermes.available ? 'text-emerald-600' : 'text-rose-600'}`}
            >
              Hermes {hermes.available ? '可用' : '不可用'}
              {hermes.bin ? ` · ${hermes.bin}` : ''}
            </Badge>
          ) : null}
        </div>

        {!hermes?.available ? (
          <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-[12px] text-amber-700 dark:text-amber-300 flex gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              当前环境未检测到 Hermes CLI。请在「设置 → Hermes」配置可执行路径，并确保 AlphaMind 后端运行在本机（非 Docker 隔离环境）。
            </div>
          </div>
        ) : null}
      </div>

      {skills.length === 0 ? (
        <div className="card p-8 text-center text-[13px] text-muted-foreground">
          未发现本地 skill。请确认 ~/.claude/skills 或 ~/.cursor/skills 下有含 SKILL.md 的目录，或在设置中配置额外扫描路径。
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {skills.map(skill => (
            <div key={skill.slug} className="card p-4 md:p-5 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[14px] font-semibold truncate">{skill.display_name}</h3>
                    {skill.enabled ? (
                      <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600">
                        <Check className="w-3 h-3" /> 已启用
                      </span>
                    ) : null}
                  </div>
                  <p className="text-[11px] text-muted-foreground font-mono mt-0.5">{skill.slug}</p>
                </div>
                <Switch
                  checked={skill.enabled}
                  disabled={toggling === skill.slug}
                  onCheckedChange={() => void toggleEnabled(skill)}
                />
              </div>
              <p className="text-[12px] text-muted-foreground line-clamp-3">
                {skill.description || '暂无描述'}
              </p>
              <div className="text-[10px] text-muted-foreground space-y-1 font-mono break-all">
                <div>路径: {skill.skill_path || '--'}</div>
                <div>来源: {skill.source_root || '--'}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
