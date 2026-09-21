export interface PortfolioPageCoreLoaderApi<StockData, PortfolioData> {
  loadStocks: (signal: AbortSignal) => Promise<StockData>
  loadPortfolio: (signal: AbortSignal) => Promise<PortfolioData>
}

export interface PortfolioPageBackgroundLoaderApi<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData> {
  loadMarketStatus: (signal: AbortSignal) => Promise<MarketStatusData>
  buildQuoteItems: (stocks: StockData, portfolio: PortfolioData) => Array<{ symbol: string; market: string }>
  loadQuotes: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<QuoteData>
  loadSuggestions: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<SuggestionData>
  loadPriceAlerts: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<AlertData>
  loadKlines: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<KlineData>
}

export interface PortfolioPageBackgroundData<MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData> {
  marketStatus: MarketStatusData
  quotes: QuoteData
  suggestions: SuggestionData
  priceAlerts: AlertData
  klines: KlineData
}

export async function loadPortfolioPageCoreData<StockData, PortfolioData>(
  api: PortfolioPageCoreLoaderApi<StockData, PortfolioData>,
  signal: AbortSignal,
): Promise<{ stocks: StockData; portfolio: PortfolioData }> {
  const [stocks, portfolio] = await Promise.all([
    api.loadStocks(signal),
    api.loadPortfolio(signal),
  ])

  return { stocks, portfolio }
}

export async function loadPortfolioPageBackgroundData<
  StockData,
  PortfolioData,
  MarketStatusData,
  QuoteData,
  SuggestionData,
  AlertData,
  KlineData,
>(
  api: PortfolioPageBackgroundLoaderApi<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData>,
  stocks: StockData,
  portfolio: PortfolioData,
  signal: AbortSignal,
): Promise<PortfolioPageBackgroundData<MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData>> {
  const items = api.buildQuoteItems(stocks, portfolio)
  const [marketStatus, quotes, suggestions, priceAlerts, klines] = await Promise.all([
    api.loadMarketStatus(signal),
    api.loadQuotes(items, signal),
    api.loadSuggestions(items, signal),
    api.loadPriceAlerts(items, signal),
    api.loadKlines(items, signal),
  ])

  return { marketStatus, quotes, suggestions, priceAlerts, klines }
}
