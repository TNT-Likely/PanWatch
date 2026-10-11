export interface DisplayQuote {
  currency?: string | null
  current_price: number | null
  change_pct: number | null
  daily_move_status?: string
  quote_date?: string | null
  is_delayed?: boolean | null
  delayed?: boolean | null
  source_timestamp?: string | null
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
  valuation_status?: 'available' | 'fx_unavailable' | 'currency_unavailable' | 'quote_unavailable'
  currency?: string | null
  exchange_rate: number | null  // 汇率（仅港股）
  daily_move_status?: string
  quote_date?: string | null
  is_delayed?: boolean | null
  delayed?: boolean | null
  source_timestamp?: string | null
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
  valuation_incomplete?: boolean
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
    valuation_incomplete?: boolean
  }
  exchange_rates?: {
    HKD_CNY: number
    USD_CNY?: number
    CAD_CNY?: number | null
    CANADA_USD_CNY?: number | null
  }
  quotes?: Record<string, { current_price: number | null; change_pct: number | null }>
}

export const toQuoteMap = (rows: QuoteResponse[]): Record<string, DisplayQuote> => {
  const map: Record<string, DisplayQuote> = {}
  for (const item of rows || []) {
    map[`${item.market}:${item.symbol}`] = {
      currency: item.currency,
      current_price: item.current_price ?? null,
      change_pct: item.change_pct ?? null,
      daily_move_status: item.daily_move_status,
      quote_date: item.quote_date,
      is_delayed: item.is_delayed ?? item.delayed,
      source_timestamp: item.source_timestamp,
    }
  }
  return map
}

export const findDisplayQuote = (
  quotes: Record<string, DisplayQuote>, market: string, symbol: string, allowLegacyAlias = false,
): DisplayQuote | null => quotes[`${market}:${symbol}`] ?? (allowLegacyAlias ? quotes[symbol] : null) ?? null

const round2 = (value: number) => Math.round(value * 100) / 100

export const mergePortfolioQuotes = (
  portfolio: PortfolioSummary | null,
  quotes: Record<string, DisplayQuote>
): PortfolioSummary | null => {
  if (!portfolio) return null

  const hkdRate = portfolio.exchange_rates?.HKD_CNY ?? 0.92
  const usdRate = portfolio.exchange_rates?.USD_CNY ?? 7.25

  const cadRate = portfolio.exchange_rates?.CAD_CNY
  const validCadRate = cadRate != null && Number.isFinite(cadRate) && cadRate > 0 ? cadRate : null
  let grandIncomplete = false
  let grandMarketValue = 0
  let grandCost = 0
  let grandValuedCost = 0
  let grandPnl = 0
  let grandAvailable = 0
  let grandDailyPnl = 0

  const accounts = portfolio.accounts.map(account => {
    let accIncomplete = false
    let accMarketValue = 0
    let accCost = 0
    let accValuedCost = 0
    let accPnl = 0
    let accDailyPnl = 0

    const positions = account.positions.map(pos => {
      const quote = quotes[`${pos.market}:${pos.symbol}`]
      const current_price = quote ? quote.current_price : pos.current_price ?? null
      const daily_move_status = quote ? quote.daily_move_status : pos.daily_move_status
      const inactive = daily_move_status != null && daily_move_status !== 'current'
      // An explicit null from the latest quote must not resurrect an older daily change.
      const change_pct = inactive ? null : quote ? quote.change_pct : pos.change_pct ?? null
      const isCanadian = pos.market === 'TSX' || pos.market === 'TSXV'
      const currency = (quote?.currency !== undefined ? quote.currency : pos.currency)?.toUpperCase()
      // Canadian USD classes require verified FX; legacy USD_CNY can be synthetic.
      const canadianUsdRate = portfolio.exchange_rates?.CANADA_USD_CNY !== undefined
        ? portfolio.exchange_rates.CANADA_USD_CNY
        : (pos.currency?.toUpperCase() === 'USD' ? pos.exchange_rate : null)
      const validCanadianUsdRate = canadianUsdRate != null && Number.isFinite(canadianUsdRate) && canadianUsdRate > 0 ? canadianUsdRate : null
      const canadianRate = currency === 'CAD' ? validCadRate : currency === 'USD' ? validCanadianUsdRate : null
      const rate = isCanadian ? canadianRate : pos.market === 'HK' ? hkdRate : pos.market === 'US' ? usdRate : 1

      const cost = rate != null ? pos.cost_price * pos.quantity * rate : 0
      if (rate == null || current_price == null) accIncomplete = true
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
          accPnl += pnl
          accValuedCost += cost
          pnl_pct = cost > 0 ? (pnl / cost * 100) : 0
        }
      }

      if (rate == null) {
        daily_pnl = null
      } else if (daily_move_status === 'closed' || daily_move_status === 'pre_market') {
        daily_pnl = 0
      } else if (current_price != null && change_pct != null && change_pct !== -100) {
        const prev = current_price / (1 + change_pct / 100)
        if (isFinite(prev) && prev > 0) {
          daily_pnl = round2((current_price - prev) * pos.quantity * rate)
          daily_pnl_pct = round2(change_pct)
          accDailyPnl += daily_pnl
        }
      }

      return {
        ...pos,
        currency: isCanadian ? currency ?? null : pos.currency,
        current_price,
        current_price_cny: current_price != null && rate != null ? current_price * rate : null,
        change_pct,
        market_value,
        market_value_cny,
        pnl,
        pnl_pct,
        daily_pnl,
        daily_pnl_pct,
        daily_move_status,
        quote_date: quote ? quote.quote_date : pos.quote_date,
        source_timestamp: quote ? quote.source_timestamp : pos.source_timestamp,
        is_delayed: quote ? quote.is_delayed ?? quote.delayed : pos.is_delayed ?? pos.delayed,
        exchange_rate: ['HK', 'US', 'TSX', 'TSXV'].includes(pos.market) ? rate : null,
        valuation_status: isCanadian && !['CAD', 'USD'].includes(currency || '') ? 'currency_unavailable' as const : rate == null ? 'fx_unavailable' as const : current_price == null ? 'quote_unavailable' as const : 'available' as const,
      }
    })

    const accPnlPct = accValuedCost > 0 ? (accPnl / accValuedCost * 100) : 0
    const accTotalAssets = accMarketValue + account.available_funds

    grandIncomplete ||= accIncomplete
    grandMarketValue += accMarketValue
    grandCost += accCost
    grandValuedCost += accValuedCost
    grandPnl += accPnl
    grandAvailable += account.available_funds
    grandDailyPnl += accDailyPnl

    return {
      ...account,
      valuation_incomplete: accIncomplete,
      total_market_value: round2(accMarketValue),
      total_cost: round2(accCost),
      total_pnl: round2(accPnl),
      total_pnl_pct: round2(accPnlPct),
      total_daily_pnl: round2(accDailyPnl),
      total_assets: round2(accTotalAssets),
      positions,
    }
  })

  const grandPnlPct = grandValuedCost > 0 ? (grandPnl / grandValuedCost * 100) : 0
  const grandTotalAssets = grandMarketValue + grandAvailable

  return {
    ...portfolio,
    accounts,
    total: {
      valuation_incomplete: grandIncomplete,
      total_market_value: round2(grandMarketValue),
      total_cost: round2(grandCost),
      total_pnl: round2(grandPnl),
      total_pnl_pct: round2(grandPnlPct),
      total_daily_pnl: round2(grandDailyPnl),
      available_funds: round2(grandAvailable),
      total_assets: round2(grandTotalAssets),
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
