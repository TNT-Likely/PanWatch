import { useRef, useState } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import { type DeepAnalysisResult } from '@panwatch/api'
import { normalizeSuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ShareCardModalProps {
  open: boolean
  onClose: () => void
  result: DeepAnalysisResult
  symbol: string
  date: string
}

/**
 * 五档评级 → 展示标签 + A股配色(红涨绿跌)。
 * 复用 technical-badge / suggestion-action 的归一化:买入/增持=红(看多)、卖出/减持=绿(看空)、持有=琥珀(中性)。
 * 这里用自包含的显式十六进制色,保证导出 PNG 在任何主题(亮/暗)下都正确。
 */
const RATING_VISUAL: Record<
  string,
  { label: string; color: string; soft: string; gradFrom: string; gradTo: string }
> = {
  // 看多(红)
  buy: { label: '买入', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fb7185', gradTo: '#e11d48' },
  add: { label: '增持', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fda4af', gradTo: '#e11d48' },
  // 中性(琥珀)
  hold: { label: '持有', color: '#d97706', soft: '#fffbeb', gradFrom: '#fbbf24', gradTo: '#d97706' },
  // 看空(绿)
  reduce: { label: '减持', color: '#059669', soft: '#ecfdf5', gradFrom: '#34d399', gradTo: '#059669' },
  sell: { label: '卖出', color: '#059669', soft: '#ecfdf5', gradFrom: '#6ee7b7', gradTo: '#059669' },
}
const RATING_FALLBACK = {
  label: '观望',
  color: '#475569',
  soft: '#f8fafc',
  gradFrom: '#94a3b8',
  gradTo: '#475569',
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
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const sug = result.raw_data?.suggestion
  // 评级来源:优先后端五档原值 rating_raw(类型未声明,运行时可能有),否则用 action,再叠加中文 action_label 兜底
  const ratingRaw = mapRatingRaw((sug as { rating_raw?: string } | undefined)?.rating_raw)
  const normalized = normalizeSuggestionAction(ratingRaw || sug?.action, sug?.action_label)
  const visual = (normalized && RATING_VISUAL[normalized]) || RATING_FALLBACK

  const stockName = parseStockName(result.title || '', symbol)
  const confidence = sug?.confidence
  const costUsd = result.raw_data?.cost_usd
  const conclusion = cleanConclusion(sug?.signal || sug?.reason || '')
  const confPct = Math.max(0, Math.min(100, (confidence ?? 0) * 10))

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${stockName}-${date}-分析卡片.png`
      link.href = dataUrl
      link.click()
    } catch (e) {
      alert(e instanceof Error ? `图片生成失败:${e.message}` : '图片生成失败,请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>分享图片</DialogTitle>
          <DialogDescription>
            导出一张干净的结论卡片,可分享到雪球 / 微信群。
          </DialogDescription>
        </DialogHeader>

        {/* 预览区:外层用主题背景,内层卡片自带显式配色 */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* 导出卡片:固定 640px 宽,所有颜色显式内联,不依赖主题 CSS 变量 */}
          <div
            ref={cardRef}
            style={{
              width: 640,
              boxSizing: 'border-box',
              background: 'linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
              borderRadius: 24,
              padding: '32px 36px',
              border: '1px solid #e2e8f0',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
              color: '#0f172a',
            }}
          >
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
              <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>
                {date}
              </div>
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
                  AI 投研结论
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
                  {visual.label}
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
                  <span>置信度</span>
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
                  分析成本 ${costUsd != null ? costUsd.toFixed(4) : '-'}
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

            {/* 分割线 */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* Footer:免责 + 品牌引流行 */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              AI 投研团队(9-Agent)深度分析 · 仅供参考,不构成投资建议
            </div>
            <div
              style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13.5,
                fontWeight: 700,
                color: '#0f172a',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 22,
                  height: 22,
                  borderRadius: 6,
                  background: visual.color,
                  color: '#ffffff',
                  fontSize: 13,
                  fontWeight: 900,
                  flexShrink: 0,
                }}
              >
                盯
              </span>
              <span>盯盘侠 PanWatch</span>
              <span style={{ color: '#cbd5e1', fontWeight: 400 }}>·</span>
              <span style={{ color: '#64748b', fontWeight: 500, fontSize: 12.5 }}>
                github.com/TNT-Likely/PanWatch
              </span>
            </div>
          </div>
        </div>

        {/* 操作区 */}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button variant="outline" size="sm" className="h-9" onClick={onClose} disabled={busy}>
            关闭
          </Button>
          <Button size="sm" className="h-9" onClick={() => void handleDownload()} disabled={busy}>
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ImageDown className="w-3.5 h-3.5" />
            )}
            {busy ? '生成中…' : '下载图片'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
