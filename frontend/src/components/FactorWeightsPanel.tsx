import { useCallback, useEffect, useMemo, useState } from 'react'
import { Scale } from 'lucide-react'
import { factorsApi, type FactorWeight } from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { useTranslation } from 'react-i18next'

const MARKET_ORDER: Record<string, number> = { CN: 0, HK: 1, US: 2 }
const FACTOR_ORDER = ['alpha_score', 'catalyst_score', 'quality_score', 'risk_penalty', 'crowd_penalty']

const formatMetric = (value: number | null | undefined, digits = 3) => {
  if (value == null || Number.isNaN(value)) return '--'
  return Number(value).toFixed(digits)
}

const rowKey = (item: FactorWeight) => `${item.market}:${item.factor_code}`

export default function FactorWeightsPanel() {
  const { t } = useTranslation('configuration')
  const factorT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = useCallback((key: string, options?: Record<string, unknown>) => factorT(`p4.components.factorWeights.${key}`, options), [factorT])
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
      setError(e instanceof Error ? e.message : tr('loadFailed'))
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [tr])

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
        setError(e instanceof Error ? e.message : tr('updateFailed'))
      } finally {
        setSaving(null)
      }
    },
    [load, tr],
  )

  return (
    <section id="sec-factors" className="card p-4 md:p-6 lg:col-span-12">
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <Scale className="w-3.5 h-3.5 text-muted-foreground" />
            {tr('title')}
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            {tr('description')}
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
        <div className="text-[12px] text-muted-foreground text-center py-6">{tr('empty')}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[11px] text-muted-foreground border-b border-border/50">
                <th className="py-2 pr-3 font-medium">{tr('factor')}</th>
                <th className="py-2 pr-3 font-medium">{tr('market')}</th>
                <th className="py-2 pr-3 font-medium text-right">{tr('weight')}</th>
                <th className="py-2 pr-3 font-medium text-right">{tr('latestIC')}</th>
                <th className="py-2 pr-3 font-medium text-right">{tr('latestIR')}</th>
                <th className="py-2 pr-3 font-medium text-right">{tr('samples')}</th>
                <th className="py-2 pr-3 font-medium text-center">{tr('pinned')}</th>
                <th className="py-2 font-medium text-center">{tr('autoCalibrate')}</th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item) => {
                const key = rowKey(item)
                const busy = saving === key
                return (
                  <tr key={key} className="border-b border-border/30 last:border-0">
                    <td className="py-2 pr-3 text-foreground">{tr(`factors.${({ alpha_score: 'alpha', catalyst_score: 'catalyst', quality_score: 'quality', risk_penalty: 'risk', crowd_penalty: 'crowding' } as Record<string, string>)[item.factor_code] || item.factor_code}`)}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{factorT(`stocksPage.markets.${({ CN: 'cn', HK: 'hk', US: 'us' } as Record<string, string>)[item.market] || item.market}`)}</td>
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
