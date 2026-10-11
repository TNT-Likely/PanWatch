# AU (ASX) Market Support

This fork adds a fourth market — Australia (ASX) — alongside CN/HK/US, plus a one-way Ghostfolio holdings sync.

## What's included

- **Market core**: `MarketCode.AU` with Sydney timezone, 10:00–16:00 AEST/AEDT trading hours, ticker pattern `^[A-Z]{1,6}$` (e.g. `VAS`, `CBA`, `WDS`). Yahoo symbols use the `.AX` suffix (`VAS` → `VAS.AX`); detection runs before the US fallback so bare 3-letter tickers can be added as AU.
- **Trading calendar**: ASX 2026 holidays (New Year, Australia Day, Good Friday, Easter Monday, Anzac Day, Queen's/King's Birthday, Christmas, Boxing Day and NSW-special days), wired into the same calendar subsystem as CN/HK/US.
- **Quotes & klines**: Yahoo (yfinance) quote vendor and Yahoo chart v8 kline vendor handle `.AX` symbols; enable via `DataSource` rows (below).
- **News**: Yahoo news vendor returns items for AU symbols when Yahoo has coverage. Live note (2026-10, NAS deployment): Yahoo's news-search index returns no items for `.AX` symbols — same as `.HK` — so AU news is expected to stay empty until another vendor is added.
- **Fractional quantities**: `Position.quantity` is a float, so DRP/micro-investing parcels sync and display correctly (`10.5` 股 style rendering preserved for whole numbers).
- **Frontend**: AU appears in every market selector (stocks, watchlist, positions, discovery, opportunities, paper trading, assistant picker); prices format as AUD (`A$`) through the existing Intl currency formatter.

## DataSource setup

The marketdata engine only consults vendors present in `data_sources`. Seed:

| type | provider | priority | supports_batch |
|------|----------|----------|----------------|
| `quote` | `yfinance` | 20 | no |
| `kline` | `yahoo` | 20 | no |
| `news` | `yahoo` | 30 | no |

Market routing needs no extra config: the engine resolves the market from the symbol itself.

## Ghostfolio sync (one-way)

Pulls a Ghostfolio export and mirrors holdings into PanWatch accounts. Contract:

- **One-way, read-only against Ghostfolio**: PanWatch never writes back; it only creates/updates/removes *its own* PanWatch accounts.
- **Prefix-scoped**: PanWatch only manages accounts whose name starts with the configured prefix (default `GF: `); accounts outside the prefix are never touched. Removals happen only inside the prefix when Ghostfolio holdings disappear.
- **Schedule**: cron (default `17 8 * * *`) registered through the same scheduler as other cron jobs, evaluated in the app timezone.
- **Toggle**: `GHOSTFOLIO_ENABLED` env (default on) mounts/unmounts the module.
- **API** (login required): `GET /api/ghostfolio/status`, `PUT /api/ghostfolio/config`, `POST /api/ghostfolio/sync/preview`, `POST /api/ghostfolio/sync`. The token is stored in PanWatch's DB and never echoed back by the status endpoint.
- **Market mapping**: `.AX` → AU base symbol; `.HK` bases are zero-padded to 5 digits (`0700.HK` → `00700`); US fallback only applies to bare symbols when the holding's data source is Yahoo.

## Calendar update policy

ASX calendar dates are embedded per year, mirroring the CN/HK/US policy: when the exchange publishes the next year's holiday list, add the dates for that year alongside the existing markets. NSW rules apply (ASX follows NSW public holidays).
