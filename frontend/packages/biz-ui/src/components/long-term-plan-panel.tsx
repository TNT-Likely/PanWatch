import { useEffect, useState } from 'react'
import {
  stocksApi,
  DEFAULT_INVESTMENT_PROFILE,
  type InvestmentProfile,
  type InvestmentProfileEvaluateResult,
  type StockItem,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface LongTermPlanPanelProps {
  stock: Pick<StockItem, 'id' | 'symbol' | 'name' | 'market' | 'investment_profile'>
  onSaved?: (profile: InvestmentProfile) => void
}

const ROLE_LABEL: Record<string, string> = {
  core: '核心仓',
  satellite: '卫星仓',
  watch: '观察',
}

export function LongTermPlanPanel({ stock, onSaved }: LongTermPlanPanelProps) {
  const { toast } = useToast()
  const [profile, setProfile] = useState<InvestmentProfile>({
    ...DEFAULT_INVESTMENT_PROFILE,
    ...(stock.investment_profile || {}),
  })
  const [evalResult, setEvalResult] = useState<InvestmentProfileEvaluateResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [loadingEval, setLoadingEval] = useState(false)

  useEffect(() => {
    setProfile({
      ...DEFAULT_INVESTMENT_PROFILE,
      ...(stock.investment_profile || {}),
    })
  }, [stock.id, stock.investment_profile])

  const updateLevel = (index: number, field: 'drawdown_pct' | 'budget_pct', value: string) => {
    const levels = [...(profile.add_plan?.levels || [])]
    const num = parseFloat(value)
    if (!levels[index]) return
    levels[index] = { ...levels[index], [field]: Number.isFinite(num) ? num : 0 }
    setProfile({ ...profile, add_plan: { ...profile.add_plan, levels } })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await stocksApi.updateInvestmentProfile(stock.id, profile)
      const saved = res.investment_profile || profile
      setProfile({ ...DEFAULT_INVESTMENT_PROFILE, ...saved })
      onSaved?.(saved)
      toast('长线计划已保存', 'success')
    } catch {
      toast('保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleEvaluate = async () => {
    setLoadingEval(true)
    try {
      const res = await stocksApi.evaluateInvestmentProfile(stock.id)
      setEvalResult(res)
    } catch {
      toast('评估失败', 'error')
    } finally {
      setLoadingEval(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-border/60 p-3">
        <div>
          <div className="text-sm font-medium">启用长线计划</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            启用后 AI 将按核心/卫星仓与分批加仓纪律给建议
          </div>
        </div>
        <Switch
          checked={profile.long_term_enabled}
          onCheckedChange={checked => setProfile({ ...profile, long_term_enabled: checked })}
        />
      </div>

      {profile.long_term_enabled && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>仓位角色</Label>
              <Select
                value={profile.portfolio_role}
                onValueChange={val => setProfile({ ...profile, portfolio_role: val })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="core">核心仓</SelectItem>
                  <SelectItem value="satellite">卫星仓</SelectItem>
                  <SelectItem value="watch">观察</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end">
              <Badge variant="secondary">{ROLE_LABEL[profile.portfolio_role] || '观察'}</Badge>
            </div>
            <div>
              <Label>目标仓位 (%)</Label>
              <Input
                value={profile.target_weight_pct ?? ''}
                onChange={e => setProfile({
                  ...profile,
                  target_weight_pct: e.target.value === '' ? null : parseFloat(e.target.value),
                })}
                placeholder="如 15"
                inputMode="decimal"
              />
            </div>
            <div>
              <Label>最大仓位 (%)</Label>
              <Input
                value={profile.max_weight_pct ?? ''}
                onChange={e => setProfile({
                  ...profile,
                  max_weight_pct: e.target.value === '' ? null : parseFloat(e.target.value),
                })}
                placeholder="如 25"
                inputMode="decimal"
              />
            </div>
          </div>

          <div>
            <Label>持有逻辑</Label>
            <textarea
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[80px]"
              value={profile.thesis}
              onChange={e => setProfile({ ...profile, thesis: e.target.value })}
              placeholder="为什么长期持有这只股票？"
              rows={3}
            />
          </div>

          <div>
            <Label className="mb-2 block">分批加仓档位</Label>
            <div className="space-y-2">
              {(profile.add_plan?.levels || []).map((lv, idx) => (
                <div key={idx} className="grid grid-cols-2 gap-2">
                  <Input
                    value={String(lv.drawdown_pct)}
                    onChange={e => updateLevel(idx, 'drawdown_pct', e.target.value)}
                    placeholder="跌幅 %"
                  />
                  <Input
                    value={String(lv.budget_pct)}
                    onChange={e => updateLevel(idx, 'budget_pct', e.target.value)}
                    placeholder="预算 %"
                  />
                </div>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">
              相对成本跌幅触发，预算比例为剩余可加仓额度的占比
            </p>
          </div>
        </>
      )}

      <div className="flex items-center gap-2">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? '保存中…' : '保存计划'}
        </Button>
        {profile.long_term_enabled && (
          <Button variant="outline" onClick={handleEvaluate} disabled={loadingEval}>
            {loadingEval ? '评估中…' : '评估加仓'}
          </Button>
        )}
      </div>

      {evalResult && (
        <div className="rounded-xl bg-accent/30 p-3 text-[12px] space-y-1">
          <div>相对成本：{evalResult.current_drawdown_pct.toFixed(1)}%</div>
          <div>当前仓位：{evalResult.weight_pct.toFixed(1)}%</div>
          {evalResult.eligible ? (
            <div className="text-emerald-600">
              可计划内加仓：约 {evalResult.suggested_amount.toFixed(0)} 元
              {evalResult.suggested_qty > 0 ? ` / ${evalResult.suggested_qty} 股` : ''}
            </div>
          ) : (
            <div className="text-muted-foreground">
              {evalResult.blockers.length > 0
                ? evalResult.blockers.join('；')
                : '尚未触发加仓档位'}
            </div>
          )}
          {evalResult.next_trigger_price != null && (
            <div>下一档触发价约：{evalResult.next_trigger_price}</div>
          )}
        </div>
      )}
    </div>
  )
}

export default LongTermPlanPanel
