export type StockTradingAskKind = 'open' | 'add' | 'reduce' | 'clear'

export function formatStockTradingAskQuestion(stockName: string, kind: StockTradingAskKind): string {
  const name = (stockName || '').trim() || '这只股票'
  const base = '请综合缠论结论、产业周期视角、深度分析（若有）、技术面与我的账户/持仓情况'
  switch (kind) {
    case 'open':
      return `${name} 现在是否可以建仓？${base}给出建议。`
    case 'add':
      return `${name} 现在是否可以加仓？${base}，并检查长线计划、最大仓位和今日交易记录。`
    case 'reduce':
      return `${name} 现在是否可以减仓？${base}，并参考今日交易记录。`
    case 'clear':
      return `${name} 现在是否要清仓？${base}，重点评估长期逻辑是否仍然成立。`
  }
}
