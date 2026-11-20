# PanWatch mobile API v1

The mobile client connects directly to the user's HTTPS PanWatch instance. The capabilities check is public. Creating, approving, cancelling, listing and revoking connections requires a signed-in Web owner. A configured Web password is required for pairing. A device token grants read-only access to the portfolio for the instance that issued it.

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
  "portfolio_read": true
}
```

For a version without a mobile endpoint, the public health and version responses can identify an old PanWatch instance. A proxy page, unsupported route, or version that cannot be identified should be presented as a service check failure. The published I0 service release determines the minimum compatible version.

## Pairing and device routes

All paths below are relative to `/api/mobile/v1`.

| Route | Authority | Request or outcome |
| --- | --- | --- |
| `POST /pairings` | Web | Submit `{"origin":"https://panwatch.example"}` to create a three-minute QR. |
| `POST /pairings/{id}/claim` | Phone | Provide the QR `challenge`, a random 32-byte lowercase hex `claim_key`, `device_name`, and `platform` (`ios` or `android`). The first claim locks the code to this key. Retrying with the same key returns the same verification code. |
| `GET /pairings/{id}` | Web | Read the current state and the claimed phone's name, platform and six-digit verification code. |
| `GET /pairings/{id}/status` | Phone | Use the claim key as the Bearer value to poll the same state. |
| `POST /pairings/{id}/decision` | Web | Submit `decision` (`approve`, `deny` or `cancel`). Approval and denial also provide the `verification_code` displayed by the phone. |
| `POST /pairings/{id}/exchange` | Phone | Send the claim key as the Bearer value after approval. A successful exchange returns `instance_id`, `device_id`, `origin`, `access_token`, `scope` and `expires_at`. The approved connection can only be exchanged once. A phone that loses the exchange response can create a fresh connection. |
| `GET /devices` | Web | List connected devices with their name, platform, creation, expiration and revocation times. |
| `DELETE /devices/{id}` | Web | Revoke the selected device and return its current record. Repeating the action preserves the original revocation time. |
| `GET /portfolio` | Phone | Send the issued access token as the Bearer value. |

The QR is the JSON object returned under `data` when creating a pairing. Its fields are `kind` (`panwatch-pairing`), `protocol` (`1`), `pairing_id`, normalized `origin`, `challenge` and `expires_at`. It contains short-lived pairing material. The phone displays the confirmed origin before claiming and verifies the same origin in the grant before storing it.

Pending states are `waiting_for_device`, `waiting_for_approval` and `approved`. Terminal states are `denied`, `cancelled`, `redeemed` and `expired`. Device grants expire after seven days and provide `portfolio:read`. Store grants in the OS secure credential store and clear the local session and displayed holdings on any device authorization failure.

The API reports `mobile_pairing_not_found` for unknown claims and `mobile_pairing_closed` for a conflicting or expired transition. A device request can return `mobile_device_invalid`, `mobile_device_expired` or `mobile_device_revoked` with status 401. Web routes also use the application login errors. Invalid setup or origin returns `mobile_login_setup_required`, `mobile_https_required` or `mobile_origin_mismatch`.

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
