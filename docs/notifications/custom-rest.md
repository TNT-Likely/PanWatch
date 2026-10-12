# Custom Webhook / REST notifications

PanWatch sends a JSON POST using the familiar Apprise Custom JSON fields
(`version`, `title`, `message`, `type`), with additional delivery metadata. A
generic webhook can consume `title` and `message` without implementing a new
PanWatch-versioned protocol. A durable notification gateway can also use the
stable IDs and `Idempotency-Key` to enqueue/deduplicate before acknowledging.
PanWatch does not install a broker, create a gateway or accept incoming commands.

Existing Apprise-backed channels are unchanged. This channel retains a small,
explicit HTTP exception because the supported Custom
JSON public APIs cannot meet all of its required security/delivery guarantees.
It does **not** claim to call Apprise's Custom JSON transport. See the verified
gaps below; adding a wrapper that replaced all of Custom JSON's send logic would
not provide useful reuse.

## Setup

In Settings → Notification channels, add **Custom Webhook / REST**. Example API payload
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

## Generic JSON payload

```http
POST /v1/events
Content-Type: application/json
Authorization: Bearer <resolved token, if configured>
Idempotency-Key: panwatch-v1-<sha256 of compact JSON [source,delivery_id]>
```

```json
{
  "version": "1.0",
  "type": "info",
  "source": "PanWatch",
  "event_id": "price-alert:42:7:5fd642de22b54b0d87e28bb22150d316",
  "delivery_id": "price-alert:42:7:5fd642de22b54b0d87e28bb22150d316",
  "timestamp": "2026-10-10T12:30:00Z",
  "title": "Price alert",
  "message": "**Example** crossed your target."
}
```

`version: "1.0"` is the Apprise JSON envelope convention, not a PanWatch protocol
version. There is no `schema_version`, `content`, or `format` field and no
compatibility mode for the earlier draft. `message` contains Markdown. The
`panwatch-v1-` header prefix versions only the stable identity derivation; it is
unchanged so retries/replay do not acquire a new deduplication identity.

UTF-8 text, Markdown, and leading/trailing title/message whitespace are retained;
nonprinting controls except LF/tab are removed. The complete UTF-8 JSON body must
be at most 256 KiB; oversized events fail before sending, without truncation or
splitting into multiple requests. This is shape compatibility with Apprise's
Custom JSON, not byte-identical formatting or its default text-length/overflow
behavior. The unused `attachments` field is omitted; there are no binary/image
attachments. A receiver forwarding to Telegram
must adapt Markdown and message lengths to its chosen parse mode. `source` is a
label, not authentication; validate tokens and allowed sources at the receiver.

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

### Durable gateway behavior

For a generic webhook, 2xx means only that its HTTP handler accepted the request.
The stronger durable/replay guarantee requires the receiver to implement this
behavior (it is not implied by Apprise, HTTP, or the JSON shape):

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

## Why the focused HTTP exception remains

The [official Custom JSON API](https://appriseit.com/services/json/) supports
`json://` / `jsons://`, methods, custom headers and added/removed/remapped payload
fields. Tests exercise the **real** public `Apprise.add(dict)` → notification
path with a controlled Requests boundary on **1.6.0 and 1.13.1**, rather than
mocking Apprise itself. Default JSON fields, Unicode/Markdown, extra metadata,
Bearer/idempotency headers and message remapping are available in both versions.
Those capabilities do not solve these remaining gaps. The `trust_env` and CA-path
probes intentionally pass unsupported options to establish the missing public
hooks; they are not claims that Apprise violates its documented Boolean `verify`
contract or promises to accept Requests session options:

| Requirement | Apprise 1.6.0 / 1.13.1 public Custom JSON path | Focused exception |
| --- | --- | --- |
| Never redirect credentials | 1.6.0 does not pass `allow_redirects`; 1.13.1 supports `redirect=no` | Disable redirects for every supported version |
| Isolate ambient proxy/netrc credentials; destination-specific PEM CA | Requests uses its default environment-trusting session; no public session injection, `trust_env=False` or destination CA-context input | Explicit verified SSL context, `trust_env=False` |
| Whole-attempt deadline and cancellation that closes the socket | `cto`/`rto` are socket timeouts; synchronous Requests is run in an executor by async notification | Native async deadline and cancellation |
| Do not download/log receiver bodies or secrets | Requests eagerly reads responses; Custom JSON DEBUG includes headers in registration URLs and failed-response bodies | Stream headers only; task-scoped HTTP log redaction |
| One owner of retries, with status/Retry-After | Notification result is Boolean; it does not expose numeric status and bounded `Retry-After` needed by our policy/outbox | Return a safe typed outcome; caller owns every retry |

These are required for loopback and remote destinations alike; choosing stock
Custom JSON only when no token is configured would still weaken isolation,
deadlines, and retry semantics. Changing the JSON payload removes the old
protocol concern but does not remove these independent transport gaps.

`custom_rest.py` owns configuration validation, environment resolution, payload
size/encoding, and stable delivery identity. `webhook_transport.py` contains only
the bounded POST and safe HTTP outcome classification; it has no URL scheme
registry, generic method/header/template configuration, retry loop, monkeypatch,
or global environment mutation. Existing notifier policy and durable outbox
continue owning retries. No Apprise internals or alternate plugin registry are
used. The application dependency stays `apprise>=1.6.0`.

The exception can be removed when supported public Custom JSON APIs provide an
injectable environment-isolated, cancellable HTTP boundary, per-destination TLS
trust, headers-only responses, secret-safe logging, and structured failure
metadata. At that point the configuration and delivery layer can directly use
Apprise without changing IDs or outbox semantics. Tagged implementations reviewed:
[1.6.0 Custom JSON](https://github.com/caronc/apprise/blob/v1.6.0/apprise/plugins/NotifyJSON.py),
[1.13.1 Custom JSON](https://github.com/caronc/apprise/blob/v1.13.1/apprise/plugins/custom_json.py).

## Verification

No persistence schema, broker deployment or gateway implementation is introduced.
Run the public capability/gap tests in separate environments with `apprise==1.6.0`
and `apprise==1.13.1`, then run the delivery regressions in each environment:

```sh
python -m pytest -q tests/test_apprise_json_contract.py tests/test_rest_notifications.py tests/test_rest_outbox.py tests/test_rest_boundaries.py tests/test_monitoring_delivery.py tests/test_template_import.py
cd frontend
pnpm run test --run tests/settings/custom-rest-channel.test.tsx
```

The REST contract CI runs these two pinned Apprise versions on Python 3.11.
The public probes pin expectations for the two reviewed versions; they do not
impose an application dependency upper bound. The REST tests deliberately use the
real notifier rather than the suite's notification no-op, with disposable local
HTTP/HTTPS receivers, temporary certificates and controlled HTTP boundaries.
They do not prove live external delivery or a real Telegram receipt.
