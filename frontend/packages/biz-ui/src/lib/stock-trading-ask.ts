export type StockTradingAskKind = 'open' | 'add' | 'reduce' | 'clear'

export function formatStockTradingAskQuestion(stockName: string, kind: StockTradingAskKind): string {
  const name = (stockName || '').trim() || '这只股票'
  const base = '请综合缠论结论、产业周期视角、深度分析（若有）、技术面、近期新闻与公告及我的账户/持仓情况；若出现警示函、监管函、立案等监管红线，默认不适合建仓/加仓，该风险优先于技术面'
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
