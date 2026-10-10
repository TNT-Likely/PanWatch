# Custom REST API notification publisher

PanWatch sends a fixed JSON POST to your notification gateway. Multiple apps can
implement this same small contract; the gateway owns the persistent queue,
Telegram integration, rate limiting and downstream retries. PanWatch does not
install a broker, create a gateway or accept incoming commands.

## Setup

In Settings → Notification channels, add **Custom REST API**. Example API payload
for the existing authenticated `/api/channels` endpoint:

```json
{
  "name": "My notification gateway",
  "type": "custom_rest",
  "enabled": true,
  "is_default": false,
  "config": {
    "endpoint_url": "https://notifications.example.net/v1/events",
    "token_env": "PANWATCH_NOTIFICATION_TOKEN",
    "tls_ca_env": "PANWATCH_NOTIFICATION_CA_PEM",
    "timeout_seconds": "5"
  }
}
```

Only `endpoint_url` is required. Choose this channel in the existing agent/alert
configuration or set it as default; saving a non-default channel alone does not
route notifications to it. The existing Test action sends a real test event.
Its success verifies gateway acceptance, not a Telegram message reaching you.

- `endpoint_url`: HTTP(S) only, no embedded credentials, query string or fragment.
  HTTPS with certificate and hostname verification is required remotely.
  Plain HTTP is accepted only for explicit `localhost`, `127.0.0.1` or `[::1]`
  loopback development, without authentication or custom CA settings. A container
  service name or LAN IP needs HTTPS. Redirects are never followed, including
  same-origin redirects; configure the final endpoint directly.
- `token_env`: optional environment variable **name**, containing a bearer token.
  Set its value in the PanWatch process using your own secret mechanism; do not
  paste it into settings. Only this reference is stored, listed or exported.
  Missing/empty variables and malformed tokens fail before sending. Environment
  setup is your deployment's responsibility; this feature creates no credentials.
- `tls_ca_env`: optional environment variable name containing PEM CA certificate
  text for a private/self-signed gateway. Configure proper trust instead of
  disabling TLS verification. Without it, system CA trust is used.
- `timeout_seconds`: a string from `1` to `10`, default `5`. It bounds the entire
  network attempt, including connect, TLS and receiving response headers.
- No arbitrary methods, headers, body templates, credential-in-URL options or
  insecure TLS switch. Requests ignore ambient proxy settings and credentials.
  This limits accidental routing or credential leakage. Gateway error response
  bodies are not downloaded or logged. This publisher's HTTP trace records are
  redacted before reaching any log handler, including the production DEBUG
  database sink; only transport phases and numeric status diagnostics remain.
  Tokens, response cookies, headers and reason phrases are omitted. Concurrent
  HTTP requests from other providers retain their existing logging behavior.

Existing API authentication/settings controls remain in force. Creation, update,
self-check, template import and runtime use the same validation. Template imports
validate REST settings before changing any selected records, including replace
imports. Exports contain environment names only, never resolved secrets.

## Wire contract (version 1)

```http
POST /v1/events
Content-Type: application/json
Authorization: Bearer <resolved token, if configured>
Idempotency-Key: panwatch-v1-<sha256 of compact JSON [source,delivery_id]>
```

```json
{
  "schema_version": 1,
  "source": "PanWatch",
  "event_id": "price-alert:42:7:5fd642de22b54b0d87e28bb22150d316",
  "delivery_id": "price-alert:42:7:5fd642de22b54b0d87e28bb22150d316",
  "timestamp": "2026-10-10T12:30:00Z",
  "title": "Price alert",
  "content": "**Example** crossed your target.",
  "format": "markdown"
}
```

UTF-8 text and Markdown are retained; nonprinting controls except LF/tab are
removed. The complete serialized body must be at most 256 KiB; oversized events
fail without truncation. This contract has no binary/image attachments. A backend
must safely adapt the indicated Markdown to its Telegram parse mode and split
long messages within Telegram limits. `source` is a label, not authentication;
validate tokens and their allowed sources at the gateway.

### Logical events and destination-specific deliveries

`event_id` identifies the logical send; generic sends selected for multiple REST
channels share that event ID and timestamp. `delivery_id` identifies an intended
delivery and is the deduplication identity. Never deduplicate globally on
`event_id` alone: distinct routes on one gateway must each enqueue a delivery.

For generic sends, `delivery_id` is `destination-` plus SHA-256 of UTF-8 compact
JSON `[source,event_id,endpoint_url,token_env]`, using validated/trimmed config and
an empty string for absent `token_env`. Different endpoint paths or auth
references yield distinct deliveries even if they share one gateway database.
The actual token value is never part of the envelope or identity. Changing only
the timeout, CA reference or token value preserves the identity; changing an
endpoint or auth reference selects a different destination. Identical destination
configurations intentionally deduplicate. Policy retries reuse the exact body
and key, independent of channel order.

Price-alert outbox rows already persist a unique event ID per channel. Their
`delivery_id` equals that persisted `event_id`, keeping both identity and timestamp
stable across new worker instances, expired leases, manual replay and channel
configuration repairs. Repairing an outbox channel does not create a second
logical delivery for a record the gateway already accepted.

This is the corrected version-1 contract for this draft feature. Gateways built
against the earlier draft must use `delivery_id` (or the new header derivation)
instead of a global `(source,event_id)` constraint before enabling multiple
REST destinations on a shared gateway.

### Required gateway behavior

1. Authenticate and validate the complete event before accepting it.
2. Atomically insert a durable queue record and a uniqueness constraint on
   `(source, delivery_id)` (or the equivalent `Idempotency-Key`). Commit this before
   returning **202 Accepted**. All 2xx statuses count as acceptance to PanWatch,
   so never return any 2xx before your intended durable acceptance boundary.
3. If the same delivery was already accepted, return 2xx without creating another
   delivery. Retain deduplication records for at least the full replay horizon,
   including operator-initiated retries; do not rely on a short in-memory cache.
4. Drain the queue independently, handle Telegram rate limits and failures, and
   store delivery receipts. An HTTP acknowledgement is not a Telegram receipt.

A lost response after commit is expected: PanWatch retries the same body and key.
The gateway's transaction prevents double enqueue. Telegram itself need not
provide exactly-once delivery, so a gateway worker crash after a Telegram send
can still cause a duplicate. Do not claim end-to-end exactly-once guarantees.

### Failures and retry ownership

- 2xx: accepted. Response bodies are ignored.
- 429: transient; honor numeric or HTTP-date `Retry-After`, clamped to 0–30 seconds.
- 5xx, network errors and timeouts: transient.
- Other statuses, including 3xx, 401/403 and other 4xx: fail without blind retry.
  Fix URL/auth/payload/gateway configuration, then explicitly retry as needed.
- Generic notification sends reuse one immutable per-destination delivery across
  notification-policy retries, capped at two additional attempts. Normal backoff
  is bounded at 2 then 4 seconds; a bounded 429 delay can take precedence. There is no hidden HTTP
  client retry layer. Existing quiet-hour policy and other providers are unchanged.
- Durable price-alert deliveries perform exactly one HTTP attempt per existing
  outbox attempt, avoiding multiplied retry budgets. Outbox backoff starts at 30
  seconds, at least the maximum supported Retry-After. The persisted event ID and
  creation timestamp survive process restarts, expired leases and manual replays.
  Permanent REST/config failures become `blocked` with `channel_config_invalid`;
  an operator can fix the channel and use existing manual retry. The legacy
  `delivered` outbox status means gateway acceptance for this channel only.
- Generic report sends do **not** gain a durable outbox in this change. Their ID
  survives in-process retries, but regenerating/re-running a report creates a new
  logical event. An application crash before gateway acceptance can lose a report
  notification. Persisting all report deliveries is a separate feature.

## Keeping this fork small

The transport and contract live in `src/platform/notifications/custom_rest.py`.
The notifier registry/dispatch, settings fields, import/API validator and existing
alert-outbox metadata are the only integration points. No persistence schema,
broker deployment or gateway implementation is introduced. On upstream sync,
check those seams and run:

```sh
python -m pytest -q tests/test_rest_notifications.py tests/test_rest_outbox.py tests/test_rest_boundaries.py tests/test_monitoring_delivery.py tests/test_template_import.py
cd frontend
pnpm run test --run tests/settings/custom-rest-channel.test.tsx
```

The REST tests deliberately use the real notifier rather than the suite's global
notification no-op. They use disposable local HTTP/HTTPS receivers and temporary
certificates, never paid LLMs, external receivers or a real Telegram bot.
