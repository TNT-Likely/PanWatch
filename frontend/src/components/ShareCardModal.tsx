import { type DeepAnalysisResult } from '@panwatch/api'
import { suggestionPresentation } from '@panwatch/biz-ui/components/suggestion-action'
import ShareCardDialog from './ShareCardDialog'
import { useTranslation } from 'react-i18next'
import { useMarketColors } from '@/hooks/use-market-colors'
import type { MarketColorPalette, MarketTone } from '@/lib/market-colors'

interface ShareCardModalProps {
  open: boolean
  onClose: () => void
  result: DeepAnalysisResult
  symbol: string
  date: string
}

/**
 * 五档评级 → 展示标签 + 当前市场涨跌配色。
 * 复用 technical-badge / suggestion-action 的归一化:买入/增持=看多、卖出/减持=看空、持有=琥珀(中性)。
 * 这里用自包含的显式十六进制色,保证导出 PNG 在任何主题(亮/暗)下都正确。
 */
type RatingVisual = {
  color: string
  soft: string
  gradFrom: string
  gradTo: string
}

function ratingVisual(tone: MarketTone): RatingVisual {
  return {
    color: tone.text,
    soft: tone.soft,
    gradFrom: tone.gradientFrom,
    gradTo: tone.text,
  }
}

function ratingVisuals(palette: MarketColorPalette): Record<string, RatingVisual> {
  return {
    // 看多
    buy: ratingVisual(palette.up),
    add: ratingVisual(palette.up),
    // 中性(琥珀)
    hold: { color: '#d97706', soft: '#fffbeb', gradFrom: '#fbbf24', gradTo: '#d97706' },
    // 看空
    reduce: ratingVisual(palette.down),
    sell: ratingVisual(palette.down),
  }
}
const RATING_FALLBACK = {
  color: '#475569',
  soft: '#f8fafc',
  gradFrom: '#94a3b8',
  gradTo: '#475569',
}
const REVIEW_VISUAL = {
  color: '#c2410c',
  soft: '#fff7ed',
  gradFrom: '#fb923c',
  gradTo: '#c2410c',
}

/** 把后端可能存在的五档原值(overweight/underweight)映射到归一化器认得的词。 */
function mapRatingRaw(raw?: string): string | undefined {
  if (!raw) return undefined
  const r = raw.toLowerCase().trim()
  if (r === 'overweight') return 'add'
  if (r === 'underweight') return 'reduce'
  return r
}

/**
 * 从标题解析股票名+代码:去掉开头的【深度】等方括号标记,去掉结尾的「:评级」。
 * 例:「【深度】广汽集团(601238):持有」→「广汽集团(601238)」
 */
function parseStockName(title: string, symbol: string): string {
  let s = (title || '').trim()
  s = s.replace(/^【[^】]*】\s*/, '') // 去掉开头第一个【...】标记
  s = s.replace(/[:：]\s*[^:：]*$/, '') // 去掉结尾「:xxx」(评级)
  s = s.trim()
  return s || symbol
}

/**
 * 清洗结论为单段:去 markdown 加粗 **,再去开头的「Action: x Reasoning:」前缀。
 * 多余空白压成单空格,便于 line-clamp 展示。
 */
function cleanConclusion(text: string): string {
  let s = (text || '').replace(/\*\*/g, '')
  s = s.replace(/^Action\s*[:：]\s*\S+\s*Reasoning\s*[:：]\s*/i, '')
  s = s.replace(/\s+/g, ' ').trim()
  return s
}

export default function ShareCardModal({ open, onClose, result, symbol, date }: ShareCardModalProps) {
  const { t } = useTranslation('configuration')
  const { palette } = useMarketColors()
  const shareT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => shareT(`p5.share.analysis.${key}`, options)
  const sug = result.raw_data?.suggestion
  // 评级来源:优先后端五档原值，否则用 action，再叠加中文 action_label 兜底。
  const ratingRaw = mapRatingRaw(sug?.rating_raw)
  const view = suggestionPresentation({ ...sug, rating_raw: ratingRaw || sug?.rating_raw })
  const normalized = view.action
  const reviewRequired = view.review
  const visuals = ratingVisuals(palette)
  const visual = reviewRequired ? REVIEW_VISUAL : (normalized && visuals[normalized]) || RATING_FALLBACK
  const visualLabel = reviewRequired
    ? shareT('p5.share.actions.review')
    : normalized && ['buy', 'add', 'hold', 'reduce', 'sell'].includes(normalized)
      ? shareT(`p5.share.actions.${normalized}`)
      : shareT('p5.share.actions.watch')

  const stockName = parseStockName(result.title || '', symbol)
  const confidence = sug?.confidence
  const usage = result.raw_data?.token_usage
  const usageLabel = usage?.recorded_calls
    ? shareT('bizUi:deepAnalysis.usage.total', { value: usage.total_tokens.toLocaleString() })
      + (usage.complete ? '' : ` · ${shareT('bizUi:deepAnalysis.usage.partial', { recorded: usage.recorded_calls, total: usage.completed_calls })}`)
    : ''
  const conclusion = cleanConclusion(sug?.signal || sug?.reason || '')
  const confPct = Math.max(0, Math.min(100, (confidence ?? 0) * 10))

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={tr('filename', { name: stockName, date })}>
      {/* Header:股票名+代码 / 日期 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>
          {stockName}
        </div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{date}</div>
      </div>

      {/* Hero:大评级 + 置信度条 + 成本 */}
      <div
        style={{
          marginTop: 20,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${visual.gradFrom} 0%, ${visual.gradTo} 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${visual.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: 1,
              opacity: 0.92,
              flexShrink: 0,
            }}
          >
            {tr('conclusion')}
          </div>
          <div
            style={{
              fontSize: 42,
              fontWeight: 900,
              lineHeight: 1,
              letterSpacing: 2,
              marginLeft: 'auto',
            }}
          >
            {visualLabel}
          </div>
        </div>

        {/* 置信度条 */}
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12.5,
              opacity: 0.92,
              marginBottom: 6,
            }}
          >
            <span>{tr('confidence')}</span>
            <span style={{ fontWeight: 700 }}>
              {confidence != null ? confidence.toFixed(1) : '-'} / 10
            </span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 999,
              background: 'rgba(255,255,255,0.3)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${confPct}%`,
                borderRadius: 999,
                background: '#ffffff',
              }}
            />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, opacity: 0.85 }}>
            {usageLabel}
          </div>
        </div>
      </div>

      {/* 结论段落:最多约 5 行 */}
      {conclusion && (
        <div
          style={{
            marginTop: 22,
            fontSize: 15.5,
            lineHeight: 1.7,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 5,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {conclusion}
        </div>
      )}

      {/* TA 卡专属副标(9-Agent),置于外壳分割线/页脚之上 */}
      <div style={{ marginTop: 22, fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
        {tr('team')}
      </div>
    </ShareCardDialog>
  )
}
