import { useRef, useState, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ShareCardDialogProps {
  open: boolean
  onClose: () => void
  /** 导出 PNG 文件名(不含扩展名)。 */
  filename: string
  /** 卡片固定宽度,默认 640。 */
  width?: number
  /** 卡片正脸内容,由各业务卡传入。颜色须显式内联,勿依赖主题 CSS 变量。 */
  children: ReactNode
}

/**
 * 分享卡通用外壳:统一的 Dialog + 固定宽度卡片容器 + 品牌页脚 + 「下载图片」按钮。
 *
 * 设计要点:
 * - 卡片容器固定宽度(默认 640px),自带白→#f8fafc 渐变背景、圆角、内边距、系统字体、显式深色文字,
 *   保证导出 PNG 在任何主题(亮/暗)下都一致。各业务卡只需提供「正脸」children。
 * - 页脚(免责 + 盯盘侠 PanWatch · github 引流行)由外壳统一渲染,作为全体分享卡的一致性锚点。
 * - 「下载图片」用 html-to-image 的 toPng(pixelRatio:2, cacheBust:true)导出为 ${filename}.png。
 */
export default function ShareCardDialog({
  open,
  onClose,
  filename,
  width = 640,
  children,
}: ShareCardDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${filename}.png`
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
          <DialogDescription>导出一张干净的卡片,可分享到雪球 / 微信群。</DialogDescription>
        </DialogHeader>

        {/* 预览区:外层用主题背景,内层卡片自带显式配色 */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* 导出卡片:固定宽度,所有颜色显式内联,不依赖主题 CSS 变量 */}
          <div
            ref={cardRef}
            style={{
              width,
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
            {/* 业务卡正脸 */}
            {children}

            {/* 分割线 */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* 页脚:免责 + 品牌引流行(全体分享卡一致) */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              仅供参考,不构成投资建议
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
                  background: '#0f172a',
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
