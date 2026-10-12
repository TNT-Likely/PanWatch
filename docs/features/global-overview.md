# Grouped global market overview

The homepage shows CSI 300, ChiNext, Hang Seng, Nasdaq Composite and S&P 500 by default. All 29 indicative observations in nine groups remain available in Customize; groups with no visible instruments do not leave empty headings. Visibility and order (both groups and individual instruments) are saved in this browser under `panwatch.globalOverview.v1` only after a customization. Existing saved layouts, including show-all, are preserved. Reset clears this preference and restores the five-index default, including when market data is still loading. Preferences are not synced between devices. No holdings, securities market codes, trading sessions or valuation rules change.

## Sources, identity and licensing

**These public feeds are not an exchange-licensed real-time product. Public access does not grant redistribution rights. Yahoo's coverage notice restricts redistribution; ChinaBond's curve portal describes a product-use application requirement. Verify provider permissions before public/commercial redistribution. This feature is intended for a personal, self-hosted reference dashboard, not order execution. No accounts, credentials, paid subscriptions or terms acceptance are added.**

| Group | Instrument | Provider symbol / series |
|---|---|---|
| China | CSI 300 / STAR 50 / ChiNext | `000300.SS` / `000688.SS` / `399006.SZ` |
| Hong Kong | Hang Seng / Hang Seng TECH | `^HSI` / `HSTECH.HK` |
| US | S&P 500 / Nasdaq Composite / Dow Jones | `^GSPC` / `^IXIC` / `^DJI` |
| Canada | S&P/TSX Composite | `^GSPTSE` |
| Europe | CAC 40 / DAX / FTSE 100 / EURO STOXX 50 | `^FCHI` / `^GDAXI` / `^FTSE` / `^STOXX50E` |
| Asia | Nikkei 225 / VN Index | `^N225` / `^VNINDEX.VN` |
| Commodities | COMEX gold / NYMEX WTI crude / CBOT corn futures | `GC=F` / `CL=F` / `ZC=F` |
| Crypto | Bitcoin / Ethereum in USD | `BTC-USD` / `ETH-USD` |
| US government yields | 3 months / 5 years / 10 years | Treasury `BC_3MONTH` / `BC_5YEAR` / `BC_10YEAR` |
| Canada government yields | 3 months / 5 years / 10 years | Valet `TB.CDN.90D.MID` / `BD.CDN.5YR.DQ.YLD` / `BD.CDN.10YR.DQ.YLD` |
| China government yields | 3 months / 5 years / 10 years | ChinaBond Government Bond Yield Curve `3M` / `5Y` / `10Y` |

The first 20 instruments use Yahoo Finance's public, undocumented chart endpoint. Symbol identity is checked before accepting observations. Index levels are **points**, with local currency context, not tradable security prices. Futures are provider rolling front-contract quotes, **not spot prices and not back-adjusted continuous returns**; roll gaps can appear. Gold is USD/troy ounce, WTI USD/barrel, corn US cents/bushel. Corn is the sole agricultural benchmark (no copper): CME reported 2025 corn average daily volume of 437,000 contracts versus 293,000 soybean contracts, and corn connects food, animal feed and ethanol supply chains.

Government observations are daily yield levels in percent. The change is `(latest yield - previous available yield) × 100` **basis points**, never relative percent return. Three months is ultra-short; five years is medium; ten years is the comparable long benchmark (not the longest issued maturity). US values are interpolated Treasury par constant-maturity yields; Canada is Treasury-bill mid-market/selected benchmark-bond yields; China is the onshore RMB government yield curve. Methodologies differ and are not directly interchangeable instruments.

## Freshness, history and failure behavior

- `/api/market/overview` is additive; `/api/market/indices` remains unchanged for existing clients.
- Five-minute in-process cache and single-flight refresh; up to four concurrent provider jobs, bounded HTTP timeout, no retries, and a 40-second aggregate refresh deadline that preserves completed feeds and marks unfinished ones unavailable. The overview has an independent loading state and a 60-second frontend timeout (the verified cold refresh took about 27 seconds in a proxied test environment). Other dashboard sections do not wait for it. One provider failure leaves other instruments available; every configured card remains present with null values and `unavailable`, never fabricated zeroes.
- Quotes include source `as_of`, exchange timezone, currency, type, status, provider and provider symbol. Source times are shown in the specified exchange timezone; daily yields show a date without pretending to have an intraday timestamp.
- `delayed` means indicative/delayed, never a promise of live prices. Old observations are retained with `stale` after four calendar days for exchange instruments, 36 hours for crypto, or seven days for daily yields. These conservative thresholds are not exchange-calendar/session checks, so long holidays may be marked stale.
- Charts contain at most 20 actual non-null daily observations. A series with fewer than two points is explicitly unavailable. Verified on 2026-10-10, the three China indices, HSTECH and VN Index returned only one daily point; those feeds have a quote but no chart.
- Changes prefer explicitly provided same-session quote changes. Calendar-session assets can otherwise use the immediately prior dated daily close. Futures require an explicit `regularMarketChange`, or `fulldayChange` with a matching `fulldayPrice`: daily labels and `currentTradingPeriod` calendar windows do not prove which overnight session a quote belongs to. Without an explicit change, futures keep their quote/history but leave previous close and both change fields null, even on the current date.
- Zero and negative futures prices and previous closes are retained. An explicit change amount is preserved; percentage change is null when the previous close is zero or negative, rather than imposing a return convention. Futures movement/chart color follows the signed price-change amount, so a rise from a negative baseline is not colored as a fall. Unknown change amounts remain neutral. Positive-baseline percentage calculations are unchanged, including a drop through zero.
- Yahoo daily bars are dated in the response's `exchangeTimezoneName`, independently of the display timezone (for example, corn bars use New York while the card displays Chicago). For calendar-session assets, the quote's own provider-local date excludes its partial daily bar, including for stale/weekend quotes; the named timezone handles historical DST offsets. Missing or invalid provider timezone metadata leaves the derived change unavailable while retaining the quote and history. An invalid prior bar does not silently substitute an older session. Crypto provider changes may use a rolling 24-hour basis. Yahoo's `chartPreviousClose` is **not used**: for a multi-day range it can mean the beginning of that range, not yesterday.
- US data requests current and previous month, Canada the latest 30 observations, China the last 60 calendar days. Missing dates/holidays are not filled. China uses server-rendered public table cells, ignores HTML comments, and never executes page scripts.
- Provider rate limits, undocumented schema changes, authentication changes or licensing limitations can make a feed unavailable. No fallback substitutes an ETF, another index, a spot instrument or stale cached value labeled live.

## Verified primary references

- [Yahoo exchange coverage and data disclaimers](https://help.yahoo.com/kb/account/SLN2310.html)
- [Yahoo historical data availability](https://help.yahoo.com/kb/finance/historical-prices-sln2311.html)
- [US Treasury XML feed documentation](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)
- [Bank of Canada Valet documentation](https://www.bankofcanada.ca/valet/docs)
- [Bank of Canada daily money-market series metadata](https://www.bankofcanada.ca/valet/groups/money_market/json)
- [ChinaBond public curve history](https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/showHistory?locale=en_US)
- [ChinaBond curve portal/use notice](https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=en_US)
- [CME 2025 volume report](https://www.cmegroup.com/media-room/press-releases/2026/1/05/cme_group_reportsrecordannualadvof281millioncontractsin2025up6ye.html)
- [CME corn's supply-chain role](https://www.cmegroup.com/articles/2023/why-corn-is-king.html)

## Keeping the fork easy to synchronize

Feature implementation lives in new `global_overview.py`, `GlobalMarketOverview.tsx`, localized resource blocks, tests and this document. Existing integration points are intentionally small:

1. `src/modules/market/api/market.py`: one additive endpoint delegates to the collector.
2. `frontend/packages/api/src/dashboard.ts`: additive typed response and client method.
3. `frontend/src/pages/Dashboard.tsx`: fetch the new method and render one component in place of the five-card strip.
4. Both locale `configuration.ts` files: one additive `globalOverview` resource block (inline because the repository translation guard requires literal resources).
5. Dashboard loading test: one mock for the new client method.

When syncing upstream, preserve those hooks or adapt them to upstream's equivalent API and dashboard boundaries. Do not cherry-pick the independent Canada holdings or other feature branches as prerequisites. Re-run:

```
PYTHONPATH=packages/marketdata/src python -m unittest discover -s tests -p 'test_global_overview*.py' -v
python -m pytest tests/test_market_indices_spark.py tests/test_index_routing.py -q
pnpm --dir frontend exec vitest run tests/dashboard tests/api/global-overview.test.ts
pnpm --dir frontend check:i18n
pnpm --dir frontend check:market-colors
pnpm --dir frontend build
```

The feature-specific PR workflow covers focused tests and a production frontend build. Public feed checks are separate manual read-only checks; offline fixture tests do not establish ongoing provider availability or redistribution rights.
