import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  return (
    <div className="space-y-3">
      {/* 趋势与形态（带说明）*/}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
          <HoverPopover
            title="趋势（均线排列）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什么：</span>
                  趋势标签来自均线（MA5/MA10/MA20）的相对位置，基于日K收盘价计算。MA越短越敏感，越长越平滑。
                </div>
                <div>
                  <span className="font-medium text-foreground">常见解读：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">多头排列</span>（MA5 &gt; MA10 &gt; MA20）：上升趋势更“顺”，回调通常先看 MA5/MA10 的支撑。</li>
                    <li><span className="font-medium text-foreground">空头排列</span>（MA5 &lt; MA10 &lt; MA20）：下降趋势占优，反弹到 MA10/MA20 往往遇到压力。</li>
                    <li><span className="font-medium text-foreground">均线交织</span>：震荡/换手期，信号更依赖成交量与关键价位。</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">{s.trend}</span>}
          />
        )}

        {s.macd_status && (
          <HoverPopover
            title="MACD（趋势/动能）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什么：</span>
                  MACD 由两条线（DIF/DEA）与柱体（hist）组成。常见口径：DIF=EMA12-EMA26，DEA=EMA(DIF,9)，hist≈(DIF-DEA)*2。
                </div>
                <div>
                  <span className="font-medium text-foreground">代表什么：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">金叉</span>：DIF 上穿 DEA，短线动能由弱转强。</li>
                    <li><span className="font-medium text-foreground">死叉</span>：DIF 下穿 DEA，短线动能由强转弱。</li>
                    <li><span className="font-medium text-foreground">柱体正/负</span>：正值通常表示多头动能占优；负值通常表示空头动能占优。</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">MACD {s.macd_status}</span>}
          />
        )}

        {s.rsi_status && (
          <HoverPopover
            title="RSI（相对强弱）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什么：</span>
                  RSI 衡量一段时间内上涨与下跌力度的相对强弱（0-100）。这里展示的是 RSI6（近6个交易日）。
                </div>
                <div>
                  <span className="font-medium text-foreground">阈值参考：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li>RSI6 &gt; 80：超买（回撤风险更高）</li>
                    <li>RSI6 70-80：偏强（动能偏多）</li>
                    <li>RSI6 &lt; 20：超卖（反弹概率提升）</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<span className={`px-2 py-0.5 rounded ${
              s.rsi_status === '超买' ? 'bg-rose-500/10 text-rose-600' :
              s.rsi_status === '超卖' ? 'bg-emerald-500/10 text-emerald-600' :
              'bg-accent/50 text-muted-foreground'
            } cursor-help hover:bg-accent/70`}>RSI {s.rsi_status}{s.rsi6 != null && ` (${s.rsi6.toFixed(0)})`}</span>}
          />
        )}

        {s.kdj_status && (
          <HoverPopover
            title="KDJ（转折/超买超卖）"
            content={<div>J 值更敏感，金叉/死叉用于观察短期转折，但容易受震荡干扰，需结合趋势与量价。</div>}
            trigger={<span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground cursor-help hover:bg-accent/70">KDJ {s.kdj_status}</span>}
          />
        )}

        {s.volume_trend && (
          <HoverPopover
            title="量能（成交量配合）"
            content={<div>放量常用于确认突破或反弹有效性；缩量上冲/下跌容易“虚”。与趋势、关键位结合更可靠。</div>}
            trigger={<span className={`px-2 py-0.5 rounded ${
              s.volume_trend === '放量' ? 'bg-amber-500/10 text-amber-600' :
              s.volume_trend === '缩量' ? 'bg-blue-500/10 text-blue-600' :
              'bg-accent/50 text-muted-foreground'
            } cursor-help hover:bg-accent/70`}>{s.volume_trend}{s.volume_ratio != null && ` (${s.volume_ratio.toFixed(1)}x)`}</span>}
          />
        )}

        {s.boll_status && (
          <HoverPopover
            title="布林带（波动/偏离）"
            content={<div>上轨/下轨的突破/跌破常见于趋势阶段或极端波动。配合量能与回踩/站稳确认有效性。</div>}
            trigger={<span className={`px-2 py-0.5 rounded ${
              s.boll_status === '突破上轨' ? 'bg-rose-500/10 text-rose-600' :
              s.boll_status === '跌破下轨' ? 'bg-emerald-500/10 text-emerald-600' :
              'bg-accent/50 text-muted-foreground'
            } cursor-help hover:bg-accent/70`}>布林 {s.boll_status}</span>}
          />
        )}

        {s.kline_pattern && (
          <HoverPopover
            title="K线形态（局部结构）"
            content={<div>单根形态提示意义有限，更看重所处位置（趋势/支撑压力附近）与量能配合。</div>}
            trigger={<span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-600 cursor-help hover:bg-amber-500/15">{s.kline_pattern}</span>}
          />
        )}
      </div>

      {/* 支撑压力（带说明）*/}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover
            title="支撑位（关键支撑区）"
            content={<div>接近支撑更容易止跌反弹；放量跌破可能转为压力。更偏向“区域”而非一点。</div>}
            trigger={<span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600 cursor-help hover:bg-emerald-500/15">支撑 {s.support.toFixed(2)}</span>}
          />
        )}
        {s.resistance != null && (
          <HoverPopover
            title="压力位（关键压力区）"
            content={<div>越接近压力上行越难；放量突破并站稳后，原压力往往会角色互换变为支撑。</div>}
            trigger={<span className="px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 cursor-help hover:bg-rose-500/15">压力 {s.resistance.toFixed(2)}</span>}
          />
        )}
      </div>
    </div>
  )
}
