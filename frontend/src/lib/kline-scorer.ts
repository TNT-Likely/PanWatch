import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'

export type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

export interface KlineEvidenceItem {
  text: string
  details?: string
  delta: number
  tag?: string
}

export interface KlineScoreSuggestion {
  action: Action
  action_label: string
  signal: string
  score: number
  evidence: KlineEvidenceItem[]
  tags: string[]
}

export function buildKlineSuggestion(s: KlineSummaryData, holding?: boolean): KlineScoreSuggestion {
  let score = 0
  const items: KlineEvidenceItem[] = []
  const tags: string[] = []

  const fmt = (n?: number | null, digits: number = 2): string => {
    if (n == null || Number.isNaN(n)) return '--'
    return Number(n).toFixed(digits)
  }

  const tf = s.timeframe || '1d'
  const asof = s.asof ? `截至${s.asof}` : ''

  const addItem = (text: string, delta: number = 0, tag?: string, details?: string) => {
    items.push({ text, delta, tag, details })
    score += delta
    if (tag) tags.push(tag)
  }

  // Trend
  if (s.trend?.includes('多头')) {
    addItem('均线多头排列，趋势偏强', 2, '多头', `周期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('空头')) {
    addItem('均线空头排列，趋势偏弱', -2, '空头', `周期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('交织')) {
    addItem('均线交织，趋势不明', 0, undefined, `周期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  }

  // MACD
  if (s.macd_status?.includes('金叉')) {
    addItem('MACD 金叉，短线动能偏强', 2, 'MACD金叉', `周期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_status?.includes('死叉')) {
    addItem('MACD 死叉，短线动能转弱', -2, 'MACD死叉', `周期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_hist != null) {
    if (s.macd_hist > 0.0) {
      addItem('MACD 柱体为正（动能偏多）', 1, undefined, `周期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    } else if (s.macd_hist < 0.0) {
      addItem('MACD 柱体为负（动能偏空）', -1, undefined, `周期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    }
  }

  // RSI
  if (s.rsi_status?.includes('超卖')) {
    addItem('RSI 超卖，可能存在反弹', 1, 'RSI超卖', `周期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（阈值<20）`)
  } else if (s.rsi_status?.includes('偏强')) {
    addItem('RSI 偏强，买盘占优', 1, 'RSI偏强', `周期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（阈值70-80）`)
  } else if (s.rsi_status?.includes('超买')) {
    addItem('RSI 超买，注意回调风险', -1, 'RSI超买', `周期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（阈值>80）`)
  } else if (s.rsi_status?.includes('偏弱')) {
    addItem('RSI 偏弱，短线承压', -1, 'RSI偏弱', `周期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（阈值<30）`)
  } else if (s.rsi_status?.includes('中性')) {
    addItem('RSI 中性', 0, undefined, `周期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}`)
  }

  // KDJ
  if (s.kdj_status?.includes('金叉')) {
    addItem('KDJ 金叉，短线转强', 1, 'KDJ金叉', `周期${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }
  if (s.kdj_status?.includes('死叉')) {
    addItem('KDJ 死叉，短线转弱', -1, 'KDJ死叉', `周期${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }

  // BOLL
  if (s.boll_status?.includes('突破上轨')) {
    addItem('突破布林上轨，趋势强势', 1, '突破上轨', `周期${tf} ${asof} · close: ${fmt(s.last_close)} · 上轨: ${fmt(s.boll_upper)}`)
  } else if (s.boll_status?.includes('跌破下轨')) {
    addItem('跌破布林下轨，走势偏弱', -1, '跌破下轨', `周期${tf} ${asof} · close: ${fmt(s.last_close)} · 下轨: ${fmt(s.boll_lower)}`)
  }

  // Volume
  if (s.volume_trend?.includes('放量')) {
    addItem('放量配合，资金参与度提升', 1, '放量', `周期${tf} ${asof} · 量比: ${fmt(s.volume_ratio, 1)}x`)
  } else if (s.volume_trend?.includes('缩量')) {
    addItem('缩量，动能不足', -1, '缩量', `周期${tf} ${asof} · 量比: ${fmt(s.volume_ratio, 1)}x`)
  }

  // Support / Resistance proximity
  if (s.last_close != null && s.support != null && s.support > 0) {
    if (s.last_close <= s.support * 1.02) {
      const dist = (s.last_close - s.support) / s.support * 100
      addItem('价格接近支撑位，止跌反弹概率提升', 1, '靠近支撑', `周期${tf} ${asof} · close: ${fmt(s.last_close)} · 支撑: ${fmt(s.support)} · 距离: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}%（阈值<=+2%）`)
    }
  }
  if (s.last_close != null && s.resistance != null && s.resistance > 0) {
    if (s.last_close >= s.resistance * 0.98) {
      const dist = (s.last_close - s.resistance) / s.resistance * 100
      addItem('价格接近压力位，上行空间受限', -1, '靠近压力', `周期${tf} ${asof} · close: ${fmt(s.last_close)} · 压力: ${fmt(s.resistance)} · 距离: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}%（阈值>=-2%）`)
    }
  }

  const holdingFlag = holding === true
  let action: Action
  if (holdingFlag) {
    if (score >= 3) action = 'add'
    else if (score >= 1) action = 'hold'
    else if (score <= -3) action = 'sell'
    else if (score <= -1) action = 'reduce'
    else action = 'watch'
  } else {
    if (score >= 3) action = 'buy'
    else if (score <= -2) action = 'avoid'
    else action = 'watch'
  }

  const uniqTags = Array.from(new Set(tags))
  const signal = uniqTags.length > 0 ? uniqTags.join(' / ') : '技术面中性'

  const actionLabel = (a: Action): string => {
    switch (a) {
      case 'buy': return '买入'
      case 'add': return '加仓'
      case 'reduce': return '减仓'
      case 'sell': return '卖出'
      case 'hold': return '持有'
      case 'watch': return '观望'
      case 'avoid': return '回避'
      default: return '观望'
    }
  }

  return {
    action,
    action_label: actionLabel(action),
    signal,
    score,
    evidence: items,
    tags: uniqTags,
  }
}
