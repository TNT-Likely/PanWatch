# PanWatch mobile API v1

The mobile client connects directly to the user's HTTPS PanWatch instance. The capabilities check is public. An owner can sign in on the phone with their existing PanWatch credentials or use a QR from the signed-in Web settings. Creating, approving, cancelling, listing and revoking connections in Settings requires a signed-in Web owner. Both connection methods issue a device token for read-only access to the portfolio on the instance that issued it.

The application publishes its typed success responses at `/openapi.json`. Like other PanWatch endpoints, this API returns `{ "code": 0, "success": true, "data": ..., "message": "" }`. Errors carry a stable `error_code` alongside the HTTP status. Routes reject redirects at the mobile client so a token or pairing proof stays on its selected HTTPS origin. Times are Unix seconds in UTC.

## Compatibility check

`GET /api/mobile/v1/capabilities` responds with the service identity, version, supported protocol range and available capabilities. Use a supported protocol and check that both pairing and portfolio reading are available before attempting a connection. Pairing is disabled until Web login is configured.

```json
{
  "service": "panwatch",
  "service_version": "dev",
  "protocol_min": 1,
  "protocol_max": 1,
  "pairing": true,
  "password_login": true,
  "portfolio_read": true,
  "session_refresh": true,
  "watchlist_read": true
}
```

For a version without a mobile endpoint, the public health and version responses can identify an old PanWatch instance. A proxy page, unsupported route, or version that cannot be identified should be presented as a service check failure. The published I0 service release determines the minimum compatible version.

## Pairing and device routes

All paths below are relative to `/api/mobile/v1`.

| Route | Authority | Request or outcome |
| --- | --- | --- |
| `POST /devices/login` | Instance credentials | When `password_login` is available, submit `origin`, `username`, `password`, `device_name`, and `platform` directly to the selected HTTPS instance. The response is the same scoped device grant used by QR pairing. |
| `POST /pairings` | Web | Submit `{"origin":"https://panwatch.example"}` to create a three-minute QR. |
| `POST /pairings/{id}/claim` | Phone | Provide the QR `challenge`, a random 32-byte lowercase hex `claim_key`, `device_name`, and `platform` (`ios` or `android`). The first claim locks the code to this key. Retrying with the same key returns the same verification code. |
| `GET /pairings/{id}` | Web | Read the current state and the claimed phone's name, platform and six-digit verification code. |
| `GET /pairings/{id}/status` | Phone | Use the claim key as the Bearer value to poll the same state. |
| `POST /pairings/{id}/decision` | Web | Submit `decision` (`approve`, `deny` or `cancel`). Approval and denial also provide the `verification_code` displayed by the phone. |
| `POST /pairings/{id}/exchange` | Phone | Send the claim key as the Bearer value after approval. A successful exchange returns `instance_id`, `device_id`, `origin`, `access_token`, `scope` and `expires_at`. The approved connection can only be exchanged once. A phone that loses the exchange response can create a fresh connection. |
| `GET /devices` | Web | List connected devices with their name, platform, creation, expiration and revocation times. |
| `DELETE /devices/{id}` | Web | Revoke the selected device and return its current record. Repeating the action preserves the original revocation time. |
| `POST /session/refresh` | Phone | Exchange the current refresh bearer and a random `Idempotency-Key` for a rotated grant on the same instance. |
| `GET /portfolio` | Phone | Send the issued access token as the Bearer value. |
| `GET /symbols/watchlist` | Phone | Send a watchlist-enabled access token. Returns `market`, `symbol` and `name` in the Web watchlist order. |

The QR is the JSON object returned under `data` when creating a pairing. Its fields are `kind` (`panwatch-pairing`), `protocol` (`1`), `pairing_id`, normalized `origin`, `challenge` and `expires_at`. It contains short-lived pairing material. The phone displays the confirmed origin before claiming and verifies the same origin in the grant before storing it.

Pending states are `waiting_for_device`, `waiting_for_approval` and `approved`. Terminal states are `denied`, `cancelled`, `redeemed` and `expired`. A client that omits `renewable` keeps the seven-day `portfolio:read` grant. Store each grant in the OS secure credential store and clear that connection and its displayed data when access is revoked or cannot be recovered.

For direct sign-in, perform the capability check without credentials. Submit the password only after the user confirms the HTTPS origin and the capability response supports password login. Discard the password after submitting. Existing Web login issues its Web session independently.

The API reports `mobile_pairing_not_found` for unknown claims and `mobile_pairing_closed` for a conflicting or expired transition. A device request can return `mobile_device_invalid`, `mobile_device_expired` or `mobile_device_revoked` with status 401. Web routes also use the application login errors. Invalid setup or origin returns `mobile_login_setup_required`, `mobile_https_required` or `mobile_origin_mismatch`.

## Renewable connections

When `session_refresh` is available, an updated client can send `renewable: true` on password sign-in or on the first phone claim. The Web confirmation shows the requested read-only permissions. The grant includes `refresh_token`, `refresh_expires_at`, and `watchlist_read`. The access token expires in 15 minutes. The refresh window lasts 30 days from issuance or successful renewal. Existing portfolio-only grants retain their original expiry and scope.

Use a fresh random 32-byte lowercase hex `Idempotency-Key` for each rotation. Persist it securely before sending the request. On success, atomically replace the saved grant and discard the key. If the response is lost, retry with the same refresh token and key. The server can re-emit that exact result for two minutes while it is still current. A different key with the retired refresh token receives 401. A Web revocation immediately blocks both access and refresh, including a retry of a successful rotation.

Refresh credentials are accepted only at the refresh route. Access credentials are accepted only at the permitted read endpoints. The watchlist returns 403 `mobile_scope_required` for a current portfolio-only grant. Validate that the returned device, instance, and HTTPS origin match before saving a rotated grant.

## Price alerts and history

An updated client can request `manage_alerts: true` alongside `renewable: true` when the service advertises `alerts_manage`. The Web approval lists that scope. The resulting `alerts_manage` flag stays with that device through renewal. Existing grants retain their scope. Revocation blocks alert and event requests.

| Route | Behavior |
| --- | --- |
| `GET /alerts` | Read the instance's rules and condition groups with the associated market and symbol. |
| `POST /alerts` | Create a single price threshold for an existing watchlist symbol. Send `market`, `symbol`, `comparison` (`<` or `>`), `price`, and a UUID `request_id`. Reuse the request ID when retrying an ambiguous response. |
| `POST /alerts/{id}/enabled` | Set `enabled` explicitly. The existing price and compound conditions are preserved. |
| `GET /events` | Read up to 50 recent persisted hits by default. A `limit` from 1 to 200 is supported. |
| `GET /events/{id}` | Read a specific hit by its instance-local event ID. The response includes the triggering rule, market, symbol, UTC timestamp and the stored observation and condition results. |

Rules created from the mobile client do not inherit the Web user's default outbound channels. Explicitly selected channels in Web settings continue to apply. The history endpoint uses the actual stored trigger and is available independently of push registration.

## Portfolio amounts

The total uses the same CNY calculation as the Web view. Holdings use the market currency and include market, symbol, name, quantity and available market value. Entries from separate enabled accounts can share a market and symbol. The mobile response does not expose account names or cost prices.

When valuation is `partial`, the total includes available values and recorded cash. When valuation is `unavailable`, the total is recorded cash and the holdings have no available price. A confirmed zero market value is numeric zero and a missing value is `null`.

```json
{
  "total_assets": 1250,
  "currency": "CNY",
  "valuation": "partial",
  "holdings": [
    {"market": "CN", "symbol": "600000", "name": "Example A", "quantity": 100, "market_value": 1200, "currency": "CNY"},
    {"market": "US", "symbol": "EXAMPLE", "name": "Example B", "quantity": 2, "market_value": null, "currency": "USD"}
  ]
}
```

## Portfolio metrics and service diagnostics

`GET /portfolio/metrics` uses the existing portfolio read grant and reports the current holdings basis, CNY assets, and the account and aggregate P/L figures. Partial and unavailable metrics use an explicit coverage state and a null value. The original `GET /portfolio` response retains its original fields.

Clients may request `read_diagnostics: true` with a renewable connection when `diagnostics_read` is advertised. Review that access in the QR approval or direct sign-in. Diagnostics endpoints use the explicit device grant, which stays in scope through renewal and ends on revocation.

| Route | Behavior |
| --- | --- |
| `GET /diagnostics` | List the scheduled workflows visible in Web and recent provider-level source telemetry. The view projects workflow status, timestamps and aggregate source statistics. |
| `POST /diagnostics/sources/{id}/test` | Test an enabled configured source and return its success flag, elapsed time and observed timestamp. |

`GET /symbols/{market}/{symbol}?interval=1d` reads an existing symbol using its market and code. It requires watchlist read access. The response identifies the vendor observations, currency, quote freshness, daily moving average and OHLC points for the selected interval. Weekly and monthly points use the same calendar aggregation as Web. A missing observation remains null or an empty series.

`GET /portfolio/metrics/history` optionally takes an `account_id`. It revisits current positions using dated closing prices, the instance summary's exchange rates held fixed, and the current recorded cash. Days missing a position or its conversion rate have a null value and a coverage count. The view is a current-holdings estimate.
