# NATS JetStream notification publisher

PanWatch can publish notifications to an existing NATS JetStream stream alongside
Telegram and its other channels. This integration is **outbound only**. It does
not deploy a broker, create streams/consumers, subscribe to commands, or run a
Telegram bridge. A publish is successful only after a valid acknowledgement from
the configured stream; a Core NATS fire-and-forget publish is not sufficient.

## Configure PanWatch

Install the runtime requirements (`nats-py>=2.16,<3` is included). In Settings →
Notification channels, add **NATS JetStream**. The same config is accepted by the
existing `/api/channels` API (see the application's API prefix in your install):

```json
{
  "name": "Notification bus",
  "type": "nats_jetstream",
  "enabled": true,
  "is_default": false,
  "config": {
    "server_url": "tls://nats.example.net:4222",
    "subject": "panwatch.notifications.v1",
    "stream": "PANWATCH_NOTIFICATIONS",
    "username": "panwatch-publisher",
    "password_env": "PANWATCH_NATS_PASSWORD",
    "tls_ca_env": "PANWATCH_NATS_CA_PEM",
    "timeout_seconds": "5"
  }
}
```

Select this channel in the existing agent/alert configuration, or explicitly set
it as a default channel. Merely saving a non-default channel does not route all
notifications to it. The existing Test action publishes an actual test message;
this confirms broker acceptance, not downstream Telegram delivery.

- Required: one server URL including port, a concrete subject (no wildcards), and
  the exact operator-created stream name.
- TLS certificate and hostname verification are mandatory; no insecure option is
  provided. **The broker must enable TLS `handshake_first: true`** (NATS
  Server 2.10.4+). PanWatch performs TLS before reading INFO or sending CONNECT,
  so a plaintext server cannot request authentication before encryption. Older
  INFO-first TLS endpoints are intentionally rejected. System CAs are used unless `tls_ca_env` names an environment variable
  containing a PEM CA certificate. No arbitrary certificate file is read.
- For token auth use `token_env`, or use `username` and `password_env` together.
  Do not combine them. Unauthenticated TLS is supported when the broker allows it.
- **Enter environment variable names, never secret values.** Set those variables
  in the PanWatch process using your deployment's secret mechanism and restart
  it as needed. This feature does not create or configure credentials. Missing
  variables fail before connecting. Raw credentials in URLs and unknown config
  keys are rejected by the channel API. Environment names remain visible in
  channel reads/exports; resolved values never enter those records or messages.
- `nats://127.0.0.1:4222` (also `localhost`/`[::1]`) is permitted only for local,
  unauthenticated development. Remote servers and authentication require TLS.
  For Podman bridge networking use a TLS endpoint with a certificate matching
  the service hostname; a plain `nats://nats:4222` URL is intentionally rejected.
  Local unauthenticated development can instead use host-network loopback.
- Timeout defaults to 5 seconds and accepts 1–10. Each attempt has a combined
  connect/publish deadline of twice this value, plus at most one second for close.
  Existing notification-policy retries apply, capped at two additional attempts
  for NATS, with backoff capped at 2 then 4 seconds. Connections close after each
  attempt; the client does not reconnect indefinitely. The existing durable
  alert worker retains its own retry schedule and 45-second send deadline.
- Provider exception text is deliberately suppressed; inspect broker-side logs
  for detailed diagnostics without exposing credentials in PanWatch errors.
- The global HTTP proxy setting does not apply to the NATS TCP connection.

## Envelope contract (version 1)

```json
{
  "schema_version": 1,
  "message_id": "price-alert:123:7:2ab04b7388864d00ab817d7e5af8eb31",
  "timestamp": "2026-10-10T06:00:00Z",
  "source": "PanWatch",
  "title": "Price alert",
  "content": "Existing notification content"
}
```

The payload is UTF-8 JSON, maximum 256 KiB including metadata. Oversize messages
fail explicitly, not silently truncate. Unicode and existing Markdown are
preserved; non-printing controls other than newline/tab are stripped. A consumer
must treat title/content as untrusted text and escape them for its output format.
Local image attachments are not included or uploaded. This envelope deliberately
does not infer ticker, price, severity or event type from text.

`Nats-Msg-Id` is a stable SHA-256 identity scoped to stream + subject +
`message_id`, so fan-out to two subjects within one stream is not suppressed by
stream-scoped deduplication. The envelope retains its logical event ID.
`Nats-Expected-Stream` is supplied by
the client to constrain publication to the configured stream. Ordinary sends get
a new ID/timestamp once per call, unchanged across policy retries. Price-alert
outbox sends use the persisted event ID and creation timestamp, unchanged across
worker restarts/manual retries. Identical text in two separate sends is not
mistaken for one event.

The transport is **at least once**, not end-to-end exactly once. A lost PubAck can
leave an accepted message pending for retry. Stream duplicate suppression is
bounded by its configured window. A downstream crash after sending Telegram but
before recording/acknowledging it can cause another Telegram message.

Only price alerts currently use the durable per-channel outbox. Ordinary reports
use existing in-process sends/retries and can be lost during broker downtime or
a publisher restart; this PR does not add a universal durable outbox. Quiet-hours
behavior stays in the existing pipeline (the channel Test action bypasses it).
Broker acknowledgement proves stream acceptance, not consumer or Telegram success.

## Operator-managed broker and future consumer

Provision JetStream before enabling the channel. For a small single Pi, one
file-backed replica is a practical starting point, but is not highly available:
keep data on durable storage, monitor disk use, and plan backups. Example stream
settings (illustrative, **not applied by PanWatch**):

```json
{
  "name": "PANWATCH_NOTIFICATIONS",
  "subjects": ["panwatch.notifications.v1"],
  "storage": "file",
  "retention": "limits",
  "num_replicas": 1,
  "max_age": 604800000000000,
  "max_bytes": 268435456,
  "max_msg_size": 262144,
  "discard": "new",
  "duplicate_window": 7200000000000
}
```

Durations above are nanoseconds: 7-day retention and 2-hour duplicate window.
Adjust limits/window to your actual volume, recovery period and Pi resources.
Limits retention allows independently durable consumers for multiple services;
messages can still expire or storage can fill while a consumer is offline. With
`discard: new`, a full stream rejects new publications visibly. Bound the
broker's total file/memory budget as well, so it cannot exhaust the Pi.

Give the PanWatch identity only publish permission on its exact notification
subject and subscribe permission on its response inbox (`_INBOX.>`). Do not give
it stream creation/deletion, consumer administration, command subscription, or
broad publish `>` permissions. Use a separate administrator to provision and a
separate consumer identity with only its durable consumer's pull/ack permissions.
Keep the broker reachable only from trusted services, with TLS/auth on the LAN.

The separate Telegram service should later provide:

1. An operator-created durable pull consumer with explicit ack; it keeps backlog
   while offline. Ack only after Telegram succeeds or the message is durably
   transferred to a deliberate failure destination.
2. Durable deduplication keyed by `message_id`, including a strategy for the
   unavoidable crash window around a Telegram send. Apply a documented freshness
   policy using the envelope timestamp, especially for stale price alerts.
3. Bounded exponential retries and handling for Telegram `retry_after`, network
   outages and rate limits. Preserve IDs on retries and distinguish malformed
   payloads from temporary failures.
4. Explicit failure routing/inspection for poison messages and exhausted attempts.
   JetStream `MaxDeliver` alone does not automatically create a dead-letter queue.
5. Retention and resource budgets, with tests for stream-full, expiry, consumer
   downtime and broker restarts; consider other Pi services' CPU/memory/storage.
6. Health monitoring for publish failures, broker availability/storage, consumer
   lag/oldest age, redeliveries, failure backlog and Telegram errors. A successful
   channel test is not a health guarantee for the full bridge.

No consumer, credentials or broker deployment is created by this feature.

## Upstream-sync notes

Keep `src/platform/notifications/nats_jetstream.py` independent. The small
integration hooks to check after rebasing a long-lived fork are:

- `notifier.py`: channel registry and NATS-only branch in custom-channel retries;
  optional `message_id`/`occurred_at` metadata leaves old callers compatible.
- `market/alert_delivery.py`: return persisted metadata from a claim and pass it
  only for the NATS channel; no schema migration or outbox rewrite.
- `administration/api/channels.py` and `automation/api/templates.py`: NATS-only
  pre-save/import config validation; registration rejects invalid configurations
  so config-only self-checks cannot falsely pass.
- `Settings.tsx` and both configuration locale files: channel fields/help text.
- `requirements-runtime.txt`: maintained nats-py 2.x client API.

Run `tests/test_nats_notifications.py`, `tests/test_monitoring_delivery.py`,
`tests/test_notify_policy.py`, `tests/test_selfcheck.py`, and
`tests/test_template_import.py`, plus Settings UI tests and the frontend checks.
The NATS tests explicitly override notification suppression and mock only the
socket; they do not send to a user's broker. For a real local broker test, see
`tests/test_nats_jetstream_integration.py` and its opt-in environment variable.

References: [nats-py publish API](https://nats-io.github.io/nats.py/modules.html),
[NATS authorization](https://docs.nats.io/learn/security/authorization),
[TLS-first configuration](https://docs.nats.io/learn/security/encryption),
[JetStream](https://docs.nats.io/nats-concepts/jetstream).
