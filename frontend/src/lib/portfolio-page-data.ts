export interface PortfolioPageLoaderApi<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData> {
  loadStocks: (signal: AbortSignal) => Promise<StockData>
  loadPortfolio: (signal: AbortSignal) => Promise<PortfolioData>
  loadMarketStatus: (signal: AbortSignal) => Promise<MarketStatusData>
  buildQuoteItems: (stocks: StockData, portfolio: PortfolioData) => Array<{ symbol: string; market: string }>
  loadQuotes: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<QuoteData>
  loadSuggestions: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<SuggestionData>
  loadPriceAlerts: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<AlertData>
  loadKlines: (items: Array<{ symbol: string; market: string }>, signal: AbortSignal) => Promise<KlineData>
}

export interface PortfolioPageData<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData> {
  stocks: StockData
  portfolio: PortfolioData
  marketStatus: MarketStatusData
  quotes: QuoteData
  suggestions: SuggestionData
  priceAlerts: AlertData
  klines: KlineData
}

export async function loadPortfolioPageData<
  StockData,
  PortfolioData,
  MarketStatusData,
  QuoteData,
  SuggestionData,
  AlertData,
  KlineData,
>(
  api: PortfolioPageLoaderApi<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData>,
  signal: AbortSignal,
): Promise<PortfolioPageData<StockData, PortfolioData, MarketStatusData, QuoteData, SuggestionData, AlertData, KlineData>> {
  const [stocks, portfolio, marketStatus] = await Promise.all([
    api.loadStocks(signal),
    api.loadPortfolio(signal),
    api.loadMarketStatus(signal),
  ])
  const items = api.buildQuoteItems(stocks, portfolio)
  const [quotes, suggestions, priceAlerts, klines] = await Promise.all([
    api.loadQuotes(items, signal),
    api.loadSuggestions(items, signal),
    api.loadPriceAlerts(items, signal),
    api.loadKlines(items, signal),
  ])

  return { stocks, portfolio, marketStatus, quotes, suggestions, priceAlerts, klines }
}
