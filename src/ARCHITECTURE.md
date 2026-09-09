# Backend module map

`src/platform` contains technical capabilities only: persistence, AI providers,
market-data clients, scheduling primitives, notifications, SSE events and
observability. It must never import a business module.

`src/modules` contains product capabilities. Each module owns its service
logic and may use platform adapters; it must not import another module's ORM
models or repositories. Cross-domain work is exposed through a small public
service, DTO, or event at the owning module boundary.

`src/web` is the HTTP composition layer. Routers translate requests and
responses only; database, models, and migrations live in `platform.persistence`.

## Business modules

- `assistant`: interactive PanAgent conversations and runtime adapters.
- `automation`: scheduled analysis agents, run records, and TradingAgents.
- `market`: symbols, collection, quotes, news, and alerts.
- `portfolio`: accounts, positions, diagnostics, and benchmarking.
- `research`: analysis history, context, evidence, and outcome evaluation.
- `strategy`: factor evaluation, candidates, signals, and backtests.
- `paper_trading`: simulated execution, ledger, and notifications.
- `reporting`: export and rendering capabilities.
- `administration`: settings diagnostics, tokens, and maintenance helpers.

Legacy `src.core` and `src.agents` packages are deliberately absent. New code
must import the owning module directly.
