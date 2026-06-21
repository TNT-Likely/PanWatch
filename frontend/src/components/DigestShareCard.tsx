import ShareCardDialog from './ShareCardDialog'

/** digest 单条:与 Dashboard 的 feed(CurateCandidate & { why }）同构。 */
export interface DigestItem {
  type: string // alert | holding | watch | risk | opportunity
  name?: string
  symbol?: string
  why: string
  change_pct?: number | null
}

interface DigestShareCardProps {
  open: boolean
  onClose: () => void
  date: string
  items: DigestItem[]
}

const UP = '#e11d48'
const DOWN = '#059669'

function moveColor(v?: number | null): string {
  if (v == null || !isFinite(v)) return '#94a3b8'
  if (v > 0) return UP
  if (v < 0) return DOWN
  return '#94a3b8'
}
function pct(v?: number | null): string {
  if (v == null || !isFinite(v)) return ''
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** 各类型的徽标:文字 + 配色(emoji 作图标,纯文本可被 PNG 正确渲染,无外部图片)。 */
const TYPE_BADGE: Record<string, { label: string; icon: string; color: string; bg: string }> = {
  alert: { label: '提醒命中', icon: '🔔', color: '#e11d48', bg: '#fff1f2' },
  holding: { label: '持仓', icon: '📊', color: '#059669', bg: '#ecfdf5' },
  watch: { label: '自选', icon: '👀', color: '#475569', bg: '#f1f5f9' },
  risk: { label: '风险', icon: '⚠️', color: '#d97706', bg: '#fffbeb' },
  opportunity: { label: '机会', icon: '✨', color: '#6366f1', bg: '#eef2ff' },
}
const FALLBACK_BADGE = { label: '要点', icon: '•', color: '#475569', bg: '#f1f5f9' }

/**
 * 每日 digest 卡:今日盯盘要点(持仓异动 / 机会 / 风险 / 提醒)。保持可扫读。
 */
export default function DigestShareCard({ open, onClose, date, items }: DigestShareCardProps) {
  const list = (items || []).slice(0, 8)

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`今日盯盘-${date}`}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>今日盯盘</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{date}</div>
      </div>
      <div style={{ marginTop: 6, fontSize: 13, color: '#64748b' }}>
        持仓异动 / 机会 / 风险提醒 · AI 为你梳理的今日要点
      </div>

      {/* 要点列表 */}
      <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {list.length === 0 ? (
          <div
            style={{
              background: '#ecfdf5',
              border: '1px solid #a7f3d0',
              borderRadius: 10,
              padding: '14px 16px',
              fontSize: 14,
              color: '#065f46',
            }}
          >
            ✓ 今日暂无明显异动或触发信号
          </div>
        ) : (
          list.map((it, i) => {
            const badge = TYPE_BADGE[it.type] || FALLBACK_BADGE
            const change = pct(it.change_pct)
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  background: '#ffffff',
                  border: '1px solid #e2e8f0',
                  borderRadius: 12,
                  padding: '11px 14px',
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    background: badge.bg,
                    color: badge.color,
                    borderRadius: 8,
                    padding: '4px 9px',
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  <span style={{ fontSize: 13 }}>{badge.icon}</span>
                  {badge.label}
                </span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  {it.name && (
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 700,
                        color: '#0f172a',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {it.name}
                    </div>
                  )}
                  <div
                    style={{
                      fontSize: 12.5,
                      color: '#64748b',
                      lineHeight: 1.5,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {it.why}
                  </div>
                </div>
                {change && (
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 14,
                      fontWeight: 800,
                      color: moveColor(it.change_pct),
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {change}
                  </span>
                )}
              </div>
            )
          })
        )}
      </div>
    </ShareCardDialog>
  )
}
