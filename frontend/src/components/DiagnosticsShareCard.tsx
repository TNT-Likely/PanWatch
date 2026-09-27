import { type PortfolioDiagnostics } from '@panwatch/api'
import ShareCardDialog from './ShareCardDialog'
import { useTranslation } from 'react-i18next'

interface DiagnosticsShareCardProps {
  open: boolean
  onClose: () => void
  diag: PortfolioDiagnostics
  /** 可选:近 N 日相对大盘超额(%),有则展示在副指标里。 */
  excessReturn?: number | null
  benchmarkLabel?: string
}

const UP = '#e11d48'
const DOWN = '#059669'
const NEUTRAL = '#d97706'
const SLATE = '#0f172a'

function signColor(v?: number | null): string {
  if (v == null || !isFinite(v)) return NEUTRAL
  if (v > 0) return UP
  if (v < 0) return DOWN
  return NEUTRAL
}

function pct(v?: number | null, digits = 1): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

/**
 * 集中度(HHI)定性:0~1,越高越集中。0.4+ 偏高,0.25~0.4 适中,<0.25 分散。
 */
function hhiBand(hhi: number, tr: (key: string) => string): { label: string; color: string } {
  if (hhi >= 0.4) return { label: tr('bands.concentrated'), color: NEUTRAL }
  if (hhi >= 0.25) return { label: tr('bands.balanced'), color: SLATE }
  return { label: tr('bands.diversified'), color: DOWN }
}

function StatBox({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div
      style={{
        flex: 1,
        background: '#ffffff',
        border: '1px solid #e2e8f0',
        borderRadius: 12,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color ?? SLATE, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

/**
 * 组合体检卡。脱敏:只展示比例 / 数量 / 风险提示,绝不出现任何金额(¥)。
 * total_market_value 仅用于把 by_market 的市值换算成「占比 %」,数值本身不展示。
 */
export default function DiagnosticsShareCard({
  open,
  onClose,
  diag,
  excessReturn,
  benchmarkLabel,
}: DiagnosticsShareCardProps) {
  const { t } = useTranslation('configuration')
  const shareT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => shareT(`p5.share.diagnostics.${key}`, options)
  const marketLabel = (market: string) => {
    const key = `p5.share.markets.${market}`
    const translated = shareT(key)
    return translated === key ? market : translated
  }
  const band = hhiBand(diag.hhi, tr)
  const totalMv = diag.total_market_value || 0
  const markets = Object.entries(diag.by_market || {})
    .map(([m, v]) => ({ m, w: totalMv > 0 ? (v / totalMv) * 100 : 0 }))
    .sort((a, b) => b.w - a.w)
  const alerts = (
    diag.alert_details?.length
      ? diag.alert_details.map((alert) => {
          const market = alert.market ? marketLabel(alert.market) : ''
          return shareT(`dashboard.diagnosticAlerts.${alert.code}`, { ...alert, market, defaultValue: alert.code })
        })
      : (diag.alerts || [])
  ).slice(0, 3)
  const hasExcess = excessReturn != null && isFinite(excessReturn)

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={tr('filename')}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: SLATE }}>{tr('title')}</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{tr('subtitle')}</div>
      </div>

      {/* Hero:集中度(HHI) */}
      <div
        style={{
          marginTop: 18,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${band.color} 0%, ${band.color}cc 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${band.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1, opacity: 0.92, flexShrink: 0 }}>
            {tr('concentration')}
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
            <span style={{ fontSize: 42, fontWeight: 900, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {diag.hhi.toFixed(2)}
            </span>
            <span style={{ fontSize: 18, fontWeight: 700, marginLeft: 10 }}>{band.label}</span>
          </div>
        </div>
      </div>

      {/* 关键指标 */}
      <div style={{ marginTop: 16, display: 'flex', gap: 12 }}>
        <StatBox label={tr('positions')} value={`${diag.position_count}`} sub={tr('countUnit')} />
        <StatBox
          label={tr('maxWeight')}
          value={`${(diag.max_weight * 100).toFixed(0)}%`}
          color={diag.max_weight >= 0.4 ? NEUTRAL : SLATE}
        />
        {hasExcess && (
          <StatBox
            label={tr('recentPerformance', { benchmark: benchmarkLabel || tr('defaultBenchmark') })}
            value={pct(excessReturn)}
            color={signColor(excessReturn)}
          />
        )}
      </div>

      {/* 市场分布 */}
      {markets.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>{tr('marketDistribution')}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {markets.map(({ m, w }) => (
              <div key={m}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#475569' }}>{marketLabel(m)}</span>
                  <span style={{ color: SLATE, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                    {w.toFixed(0)}%
                  </span>
                </div>
                <div style={{ height: 8, borderRadius: 999, background: '#e2e8f0', overflow: 'hidden' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, w)}%`,
                      borderRadius: 999,
                      background: '#6366f1',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 风险提示 */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>{tr('riskAlerts')}</div>
        {alerts.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {alerts.map((a, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  background: '#fffbeb',
                  border: '1px solid #fde68a',
                  borderRadius: 10,
                  padding: '10px 12px',
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: '#92400e',
                }}
              >
                <span style={{ flexShrink: 0, fontWeight: 900 }}>!</span>
                <span>{a}</span>
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              background: '#ecfdf5',
              border: '1px solid #a7f3d0',
              borderRadius: 10,
              padding: '10px 12px',
              fontSize: 13,
              color: '#065f46',
            }}
          >
            {tr('noRisk')}
          </div>
        )}
      </div>
    </ShareCardDialog>
  )
}
