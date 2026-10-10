# TradingAgents fork integration

PanWatch installs `idkwhodatis/TradingAgents` at the immutable commit in
`requirements-runtime.txt` (currently
`c12865ae3d4be7790853a1e047d067a0a9931821`). The imported package and installed distribution must
both report 0.6.0. Updating the pin is an explicit dependency upgrade; never use
an unpinned fork branch for deployment.

This branch is independent of Canadian-market support. On its own it preserves
PanWatch's existing CN/HK/US markets. Combine it with the Canadian-market branch
to enable TSX/TSXV; changing this dependency alone does not add UI markets,
provider identities, exchange calendars or Canadian portfolio valuation.

## Small upstream boundary

`upstream_compat.py` detects official 0.5 and fork 0.6 module layouts. Only a
missing legacy module triggers fallback; a missing dependency inside that module
is not silently treated as a layout change. The adapter patches both source
modules and import-time function bindings:

- vendor router and agent tool bindings
- Yahoo OHLCV loader and technical-indicator/snapshot bindings
- verified snapshot builder and agent tool binding

The existing reference-counted router, request-local cache, cancellation,
CN interception, HK normalization and US Yahoo fallback remain unchanged.
Router bindings restore when the final nested request exits; OHLCV/snapshot
hooks retain the original process-wide installation behavior.

## Validation and upgrades

Run `python -m pytest -q tests/test_tradingagents*.py tests/test_ta*.py` against
an installed wheel, not a source directory shadowing a different distribution.
The dedicated workflow tests the pinned fork and official 0.5 layout. Tests
exercise actual installed tool/import bindings with deterministic data and no
paid LLM calls. They do not constitute a live-market or live-agent smoke test.

When syncing the fork, rebuild/install its wheel, verify source and distribution
versions match, rerun the wrapper contracts and review changed upstream import
sites before updating the commit pin. With the Canadian feature also present,
rerun its identity, valuation and exchange-fallback contracts as well.
