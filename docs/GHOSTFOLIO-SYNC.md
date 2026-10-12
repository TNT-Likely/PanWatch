# Ghostfolio One-Way Sync (Optional)

PanWatch reads a Ghostfolio export and reconciles local accounts and positions. It does not write back to Ghostfolio.

## Enablement

Sync is off by default. Set `GHOSTFOLIO_ENABLED=1` in the deployment environment to mount `/api/ghostfolio` (the default is `0`). Configure `base_url` and `api_token` and set the persisted `enabled` field to `true` through `PUT /api/ghostfolio/config`; its default is `false`. The config request also accepts `account_prefix` (default `GF: `) and `sync_cron` (default `17 8 * * *`). `GET /api/ghostfolio/status` reports the enabled state, schedule and last sync status without returning the token. The scheduled job is registered only while enabled and re-checks the flags on each run. Manual `/sync` and `/sync/preview` also check both flags; a disabled preview does not fetch the export.

## Validation and reconciliation

Before changing positions, sync requires a JSON object with a non-empty `accounts` list and an `activities` list. Every account must have an ID. Every activity must reference an account in the export, have a type and carry a date parseable by `datetime.fromisoformat` (ISO date or timestamp). BUY and SELL also require numeric quantity and unit price, and a numeric fee when provided. An unmappable BUY/SELL symbol or conflicting non-empty currencies for the same account and instrument also invalidates the export. Missing, invalid or partial exports, including an empty accounts list, abort without applying position changes. On a validation error, `last_status` records `aborted: <reason>` and sync raises the error. `/sync/preview` returns an invalid-export error instead of partial holdings.

Accounts are identified by upstream Ghostfolio account ID in `ghostfolio_account_maps`, not by their display names. Two upstream accounts with the same name get distinct local accounts. On first mapping, an unmapped existing local account whose name equals the configured prefix plus the upstream display name can be adopted once. Later name changes do not change the mapping. Only mapped local accounts are considered for removals.

On a valid export, symbols absent from an account's computed holdings are removed if that account still has other positions. If removal would empty the account, those positions are retained and returned in `pending_removals` with local `account_id`, `account_name`, `symbol` and `market`; `pending_removal_count` gives their number. To apply account-emptying removals, call `POST /api/ghostfolio/sync?confirm_empty=true`. `POST /api/ghostfolio/sync` without that parameter leaves them pending. The confirmation applies to that sync request; it is not a persisted setting.

## Activity handling

Activities are processed by calendar date, retaining export order within a day. BUY adds quantity and cost including fees; SELL reduces quantity and cost at the current average cost. A holding retains one cost currency from its activities; its cost total is expressed in that currency. Conflicting non-empty currencies within the same account and market-qualified instrument abort the sync rather than being converted or combined. Non-BUY/SELL activities are counted by type in `skipped_types` and do not affect computed holdings. Transfers and corporate actions are not reconstructed. PanWatch never writes changes to Ghostfolio.
