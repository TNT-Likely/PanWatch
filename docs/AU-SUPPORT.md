# AU (ASX) Market Support

AU (ASX) is an **opt-in, disabled-by-default** market alongside the core CN/HK/US markets. Enabling it extends quotes, daily K-lines, watchlist, and holdings to Australian securities with correct AUD valuation — nothing more. Core-market behavior is unchanged whether AU is on or off.

## Capability status

| Capability | Status | Notes |
|---|---|---|
| Quotes | Available when enabled | `yahoo_au` vendor — Yahoo chart v8, AUD-verified |
| Daily K-lines | Available when enabled | Yahoo chart v8 kline vendor (`yahoo`), AU-flagged |
| Watchlist | Available when enabled | Add/search gated by market enablement |
| Holdings (valuation) | Available when enabled | AUD → CNY via dated `AUD/CNY` rate |
| Search | Unavailable | No verified AU symbol catalog; `/stocks/search` returns nothing for AU |
| News | Unavailable | Yahoo's news index returns no items for `.AX` symbols |
| Manual analysis | Unavailable | Fundamentals inputs for AU are unverified |
| Price alerts | Unavailable | Not in the AU capability set |
| Discovery / auto-strategy | Unavailable | Core-only; AU is not in the market pool |
| Paper trading | Unavailable | No AU settlement policy yet — ASX is T+2 and needs its own calendar, fees, quantity, and freshness validation |

## Enabling AU

1. Turn the market on — set the `market_enablement` setting to `{"AU": true}`:

   ```bash
   curl -X PUT /api/settings/market_enablement -H 'Content-Type: application/json' \
     -d '{"value": "{\"AU\": true}"}'
   ```

   (or set the same key from the settings UI). Only optional markets are accepted; anything else is rejected with 400.

2. Seed the AU data sources. The marketdata engine only consults vendors registered in `data_sources`:

   | type | provider | priority |
   |---|---|---|
   | `quote` | `yahoo_au` | 20 |
   | `kline` | `yahoo` | 20 |

3. Verify: `GET /api/stocks/markets/enabled` should now list AU with capabilities `quotes, klines, watchlist, holdings` and currency `AUD`.

## Symbol rules

AU identity is normalized at the entry boundary — every accepted spelling maps to one canonical symbol:

- `VAS.AX` ≡ `VAS` with `market=AU` ≡ `vas.ax` (case-insensitive) → canonical `Symbol(AU, "VAS")`, fetched as `VAS.AX`.
- **A bare ticker stays US.** `CBA`, `AAPL`, `VAS` typed without a market all resolve as US; there is no AU auto-detection from the ticker alone. To add an AU stock you must pass `market=AU` or use the `.AX` suffix.
- An explicit market that conflicts with the suffix is rejected: `Symbol.parse("VAS.AX", market="US")` raises `ValueError` instead of silently re-classifying.

## Valuation

- AU quotes carry `currency: "AUD"`; holdings convert AUD → CNY through a dated exchange rate (`AUD/CNY`, sina `fx_saudcny`, cached 1 hour).
- Rates are resolved **by currency, not by market**, and a missing rate is reported instead of assumed: if the FX fetch fails, affected accounts are marked `valuation_status: "incomplete"` with the unvalued symbols listed, and the portfolio total carries `valuation_complete: false`. A position is never silently converted at 1:1.
- The portfolio API also reports `exchange_rates.AUD_CNY` and per-pair `fx_as_of` timestamps.

## Disabling AU

Setting `{"AU": false}` (or removing the override) takes effect immediately: every market-accepting endpoint refuses AU with `400 market_disabled`, the marketdata client skips AU without touching vendors, and automation (scans, alerts engine paths) stops selecting AU symbols. **Data is preserved** — watchlist rows, holdings, and stored quotes/K-lines remain and re-validate unchanged if AU is re-enabled.

## Known limitations

- No AU symbol search until a verified catalog vendor exists; add symbols by explicit code.
- News and fundamentals inputs are empty for AU; AI analysis flows that depend on them degrade accordingly.
- The ASX trading calendar is published per year (NSW rules); the next year's dates must be added when ASX publishes them, mirroring the CN/HK/US calendar policy.
- HKD/USD rates keep their legacy static fallbacks client-side; AUD intentionally has none.
