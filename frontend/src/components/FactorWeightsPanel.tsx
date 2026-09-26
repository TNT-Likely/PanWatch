import { useCallback, useEffect, useMemo, useState } from 'react'
import { Scale } from 'lucide-react'
import { factorsApi, type FactorWeight } from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'

const FACTOR_LABELS: Record<string, string> = {
  alpha_score: '选股α',
  catalyst_score: '催化',
  quality_score: '计划质量',
  risk_penalty: '风险',
  crowd_penalty: '拥挤度',
}

const MARKET_LABELS: Record<string, string> = {
  TW: '台股',
  US: '美股',
}

const MARKET_ORDER: Record<string, number> = { TW: 0, US: 1 }
const FACTOR_ORDER = ['alpha_score', 'catalyst_score', 'quality_score', 'risk_penalty', 'crowd_penalty']

const factorLabel = (code: string) => FACTOR_LABELS[code] || code
const marketLabel = (market: string) => MARKET_LABELS[market] || market

const formatMetric = (value: number | null | undefined, digits = 3) => {
  if (value == null || Number.isNaN(value)) return '--'
  return Number(value).toFixed(digits)
}

const rowKey = (item: FactorWeight) => `${item.market}:${item.factor_code}`

export default function FactorWeightsPanel() {
  const [items, setItems] = useState<FactorWeight[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await factorsApi.list()
      setItems(res.items || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const marketDelta = (MARKET_ORDER[a.market] ?? 99) - (MARKET_ORDER[b.market] ?? 99)
      if (marketDelta !== 0) return marketDelta
      const aFactor = FACTOR_ORDER.indexOf(a.factor_code)
      const bFactor = FACTOR_ORDER.indexOf(b.factor_code)
      return (aFactor < 0 ? 99 : aFactor) - (bFactor < 0 ? 99 : bFactor)
    })
  }, [items])

  const updateFlag = useCallback(
    async (item: FactorWeight, patch: { is_pinned?: boolean; auto_calibrate?: boolean }) => {
      const key = rowKey(item)
      setSaving(key)
      try {
        await factorsApi.update(item.factor_code, item.market, patch)
        await load()
      } catch (e) {
        setError(e instanceof Error ? e.message : '更新失败')
      } finally {
        setSaving(null)
      }
    },
    [load],
  )

  return (
    <section id="sec-factors" className="card p-4 md:p-6 lg:col-span-12">
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <Scale className="w-3.5 h-3.5 text-muted-foreground" />
            因子权重自校准
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            每因子权重由 IC/IR 每日自动标定;锁定或关闭自动标定可手动接管(仅供参考)。
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-3 text-[12px] text-amber-500">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      ) : sortedItems.length === 0 ? (
        <div className="text-[12px] text-muted-foreground text-center py-6">暂无因子权重数据</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[11px] text-muted-foreground border-b border-border/50">
                <th className="py-2 pr-3 font-medium">因子</th>
                <th className="py-2 pr-3 font-medium">市场</th>
                <th className="py-2 pr-3 font-medium text-right">权重</th>
                <th className="py-2 pr-3 font-medium text-right">最近IC</th>
                <th className="py-2 pr-3 font-medium text-right">最近IR</th>
                <th className="py-2 pr-3 font-medium text-right">样本</th>
                <th className="py-2 pr-3 font-medium text-center">锁定</th>
                <th className="py-2 font-medium text-center">自动标定</th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item) => {
                const key = rowKey(item)
                const busy = saving === key
                return (
                  <tr key={key} className="border-b border-border/30 last:border-0">
                    <td className="py-2 pr-3 text-foreground">{factorLabel(item.factor_code)}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{marketLabel(item.market)}</td>
                    <td className="py-2 pr-3 text-right font-mono text-foreground">{formatMetric(item.weight, 2)}</td>
                    <td className="py-2 pr-3 text-right font-mono text-muted-foreground">{formatMetric(item.last_ic)}</td>
                    <td className="py-2 pr-3 text-right font-mono text-muted-foreground">{formatMetric(item.last_ir)}</td>
                    <td className="py-2 pr-3 text-right font-mono text-muted-foreground">{item.last_sample_size ?? '--'}</td>
                    <td className="py-2 pr-3">
                      <div className="flex justify-center">
                        <Switch
                          checked={item.is_pinned}
                          disabled={busy}
                          onCheckedChange={(v) => updateFlag(item, { is_pinned: v })}
                        />
                      </div>
                    </td>
                    <td className="py-2">
                      <div className="flex justify-center">
                        <Switch
                          checked={item.auto_calibrate}
                          disabled={busy}
                          onCheckedChange={(v) => updateFlag(item, { auto_calibrate: v })}
                        />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
