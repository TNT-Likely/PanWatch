export interface DisplayQuote {
  current_price: number | null
  change_pct: number | null
  daily_move_status?: string
  quote_date?: string | null
}

export interface QuoteResponse extends DisplayQuote {
  symbol: string
  market: string
}

export interface Position {
  id: number
  stock_id: number
  sort_order?: number
  symbol: string
  name: string
  market: string
  asset_type?: 'stock' | 'etf'
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string  // short: 短线, swing: 波段, long: 长线
  current_price: number | null
  current_price_cny: number | null  // 人民币价格（港股换算后）
  change_pct: number | null
  market_value: number | null
  market_value_cny: number | null  // 人民币市值
  pnl: number | null
  pnl_pct: number | null
  daily_pnl: number | null
  daily_pnl_pct: number | null
  exchange_rate: number | null  // 汇率（仅港/美/澳股）
  currency?: string | null  // 报价币种；缺省时按 market 推断
  daily_move_status?: string
  quote_date?: string | null
}

export interface AccountSummary {
  id: number
  name: string
  available_funds: number
  total_market_value: number
  total_cost: number
  total_pnl: number
  total_pnl_pct: number
  total_daily_pnl: number
  total_assets: number
  positions: Position[]
}

export interface PortfolioSummary {
  accounts: AccountSummary[]
  total: {
    total_market_value: number
    total_cost: number
    total_pnl: number
    total_pnl_pct: number
    total_daily_pnl: number
    available_funds: number
    total_assets: number
    valuation_complete?: boolean  // false = 有持仓缺少币种/汇率，未计入 CNY 合计
  }
  exchange_rates?: {
    HKD_CNY: number
    USD_CNY?: number
    AUD_CNY?: number
  }
  quotes?: Record<string, { current_price: number | null; change_pct: number | null }>
}

export const toQuoteMap = (rows: QuoteResponse[]): Record<string, DisplayQuote> => {
  const map: Record<string, DisplayQuote> = {}
  for (const item of rows || []) {
    map[`${item.market}:${item.symbol}`] = {
      current_price: item.current_price ?? null,
      change_pct: item.change_pct ?? null,
      daily_move_status: item.daily_move_status,
      quote_date: item.quote_date,
    }
  }
  return map
}

const round2 = (value: number) => Math.round(value * 100) / 100

// 币种 → CNY 汇率。HK/USD 保留既有兜底值；AUD 无兜底：缺汇率即估值不完整，绝不按 1:1 折算。
const currencyFor = (pos: Position): string | null =>
  pos.currency ?? (pos.market === 'HK' ? 'HKD' : pos.market === 'US' ? 'USD' : pos.market === 'AU' ? 'AUD' : pos.market === 'CN' ? 'CNY' : null)

const rateFor = (currency: string | null, rates: PortfolioSummary['exchange_rates']): number | null => {
  if (currency === 'CNY') return 1
  if (currency === 'HKD') return rates?.HKD_CNY ?? 0.92
  if (currency === 'USD') return rates?.USD_CNY ?? 7.25
  if (currency === 'AUD') return rates?.AUD_CNY ?? null
  return null
}

export const mergePortfolioQuotes = (
  portfolio: PortfolioSummary | null,
  quotes: Record<string, DisplayQuote>
): PortfolioSummary | null => {
  if (!portfolio) return null

  let anyUnvalued = false
  let grandMarketValue = 0
  let grandCost = 0
  let grandAvailable = 0
  let grandDailyPnl = 0

  const accounts = portfolio.accounts.map(account => {
    let accMarketValue = 0
    let accCost = 0
    let accDailyPnl = 0

    const positions = account.positions.map(pos => {
      const quote = quotes[`${pos.market}:${pos.symbol}`]
      const current_price = quote?.current_price ?? pos.current_price ?? null
      const daily_move_status = quote ? quote.daily_move_status : pos.daily_move_status
      const inactive = daily_move_status != null && daily_move_status !== 'current'
      // An explicit null from the latest quote must not resurrect an older daily change.
      const change_pct = inactive ? null : quote ? quote.change_pct : pos.change_pct ?? null
      // 缺汇率的持仓保持 native 市值，CNY 字段一律 null，且不计入账户/组合合计。
      const rate = rateFor(currencyFor(pos), portfolio.exchange_rates)
      if (rate == null) anyUnvalued = true

      const cost = rate != null ? pos.cost_price * pos.quantity * rate : 0
      accCost += cost

      let market_value: number | null = null
      let market_value_cny: number | null = null
      let pnl: number | null = null
      let pnl_pct: number | null = null
      let daily_pnl: number | null = null
      let daily_pnl_pct: number | null = null

      if (current_price != null) {
        market_value = current_price * pos.quantity
        if (rate != null) {
          market_value_cny = market_value * rate
          accMarketValue += market_value_cny
          pnl = market_value_cny - cost
          pnl_pct = cost > 0 ? (pnl / cost * 100) : 0
        }
      }

      if (rate != null && (daily_move_status === 'closed' || daily_move_status === 'pre_market')) {
        daily_pnl = 0
      } else if (rate != null && current_price != null && change_pct != null && change_pct !== -100) {
        const prev = current_price / (1 + change_pct / 100)
        if (isFinite(prev) && prev > 0) {
          daily_pnl = round2((current_price - prev) * pos.quantity * rate)
          daily_pnl_pct = round2(change_pct)
          accDailyPnl += daily_pnl
        }
      }

      return {
        ...pos,
        current_price,
        current_price_cny: rate != null && current_price != null ? current_price * rate : null,
        change_pct,
        market_value,
        market_value_cny,
        pnl,
        pnl_pct,
        daily_pnl,
        daily_pnl_pct,
        daily_move_status,
        quote_date: quote ? quote.quote_date : pos.quote_date,
        exchange_rate: rate != null && (pos.market === 'HK' || pos.market === 'US' || pos.market === 'AU') ? rate : null,
      }
    })

    const accPnl = accMarketValue - accCost
    const accPnlPct = accCost > 0 ? (accPnl / accCost * 100) : 0
    const accTotalAssets = accMarketValue + account.available_funds

    grandMarketValue += accMarketValue
    grandCost += accCost
    grandAvailable += account.available_funds
    grandDailyPnl += accDailyPnl

    return {
      ...account,
      total_market_value: round2(accMarketValue),
      total_cost: round2(accCost),
      total_pnl: round2(accPnl),
      total_pnl_pct: round2(accPnlPct),
      total_daily_pnl: round2(accDailyPnl),
      total_assets: round2(accTotalAssets),
      positions,
    }
  })

  const grandPnl = grandMarketValue - grandCost
  const grandPnlPct = grandCost > 0 ? (grandPnl / grandCost * 100) : 0
  const grandTotalAssets = grandMarketValue + grandAvailable

  return {
    ...portfolio,
    accounts,
    total: {
      total_market_value: round2(grandMarketValue),
      total_cost: round2(grandCost),
      total_pnl: round2(grandPnl),
      total_pnl_pct: round2(grandPnlPct),
      total_daily_pnl: round2(grandDailyPnl),
      available_funds: round2(grandAvailable),
      total_assets: round2(grandTotalAssets),
      valuation_complete: !anyUnvalued,
    },
  }
}


export const applyMarketStatuses = (
  quotes: Record<string, DisplayQuote>,
  markets: { code: string; status: string; local_date?: string }[],
): Record<string, DisplayQuote> => {
  const statuses = new Map(markets.map(market => [market.code, market]))
  return Object.fromEntries(Object.entries(quotes).map(([key, quote]) => {
    const market = statuses.get(key.split(':')[0])
    if (!market) return [key, quote]
    const inactive = ['closed', 'pre_market', 'unknown'].includes(market.status)
    const stale = quote.quote_date && market.local_date && quote.quote_date !== market.local_date
    return [key, inactive || stale ? {
      ...quote, change_pct: null, daily_move_status: inactive ? market.status : 'stale',
    } : quote]
  }))
}
