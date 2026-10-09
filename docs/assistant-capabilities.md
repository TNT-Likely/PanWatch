# Assistant capability foundations

[简体中文](assistant-capabilities.zh-CN.md)

Evidence distinguishes external data, tool discovery and local query snapshots.
Tool discovery shows execution time; instrument lookup, watchlists, market calendars,
notification settings, capability checks, alert read/write receipts and monitoring
health show the original query snapshot time instead of market-data freshness. Historical
responses retain their captured timestamps without refreshing stored facts.
Unknown or stale external source times still produce warnings. Saved research and
mixed portfolio valuations retain source-time checks despite their local storage.

The assistant preserves source time separately from retrieval time. Quotes include
provider, source timestamp when verified, trading date, market status and freshness.
Unknown timestamps and closed or stale quotes cannot establish a live price; daily
changes remain available as source fields instead of being presented as today's move.
Quotes explicitly describe provider snapshots with no verified bar-close confirmation.
Break, closed or delayed status does not establish an official closing price, last
trade or guaranteed update time. Stateful bar-close triggers remain unsupported.

`check_watch_request` reads the original user request once and returns explicit
instruments, portfolio/watchlist scope, a supported relative horizon, requested
condition types/channels, and capability gaps. Relative expiry is anchored to task
creation and retained through approval. This is a bounded parser, not a complete
natural-language guarantee: ambiguous names require stock lookup, and unsupported
or unrecognized requirements need clarification. Bar-close, moving-average,
consecutive-session, crossing and news-release triggers are not implemented.
Explicit instrument writes preserve both symbol and market. Mixed AND/OR rules
require nested logic and are rejected rather than flattened. A completed capability
check does not mean that the requested alert exists; result details retain these gaps.
Explicit whole-turn retries such as "try again" retain the preceding user request,
its conditions and original expiry. Resolution happens before model context
compression, uses user messages only, and stops at a newer topic. Unresolved
retries require clarification; empty parsing does not establish capability support.

Alerts support flat AND/OR combinations of price, percent change, turnover, volume
and volume ratio, plus expiry, enabled channel IDs, market hours, cooldown, daily
limit and once/repeat mode. Approval displays the proposed settings; successful
writes and subsequent reads return the complete persisted rule. Zero cooldown and
zero daily limit are preserved. Channel selection does not prove message delivery.
Turnover and volume retain provider units; conversion is not inferred.

Read-only discovery includes saved watchlists, market-scoped historical suggestions
(including expired opinions), context snapshots and explicitly market-scoped legacy
reports. Legacy reports without a market are counted separately. CN announcements
include publication time and source links; detail reads verify the announcement
against its instrument and disclose missing or truncated original text. HK/US
regulatory filings remain outside the event adapter's coverage.

Portfolio diagnosis uses canonical read tools and the host permission policy.
Nested calls retain arguments, source time, original fields, failures and step IDs.
Each completed diagnostic judgment links to its underlying evidence in the result
card. Internal reads are bounded to 16 calls and the task tool-call limit; the host
tool deadline is 120 seconds and the total task deadline remains 180 seconds.

## Evaluating the production path

Prepare and start an isolated run using the project `panwatch-local-delivery` skill.
Configure the QA model via normal settings APIs, and add an enabled synthetic Feishu
channel with no real recipient and no default delivery. The evaluator only targets
that run's loopback origin and reads its database in read-only mode:

```sh
.venv/bin/python scripts/evaluate-assistant-runtime.py --run '<private QA run>'
```

It exercises deferred discovery, the actual task worker and runtime, approval before
writing, full DB readback, duplicate approval, rejection, unsupported conditions,
a real SSE client disconnect and ordered replay. The required ten cases also cover
an ordinary OR request, expired historical opinions, actual announcement text and
source-linked portfolio diagnosis. Seed an expired, market-scoped synthetic opinion
and a synthetic portfolio before first startup. New synthetic conversations and
two alerts are retained for inspection; do not run it over a human's acceptance data.
The 999999 price threshold is a synthetic non-triggering fixture, not an investment
recommendation. Use `--case` to select cases and `--mode replay` for a scripted
provider. Report paths contain no credentials. Notification delivery, every possible
utterance, all models and real network outages are separate acceptance layers.

Exit codes: `0` = all required live-model cases pass, `1` = executed assertion failed,
`2` = incomplete (including replay-only or selected-case coverage). The legacy
`tests/eval/run_eval.py` does not cover production runtime and now exits `2` when the
model is absent; `--allow-incomplete` explicitly permits a partial local check.
Unit tests with a scripted provider remain contract tests and cannot establish
live-model quality. Preserve failed attempts and report each layer independently.
PASS describes the listed assertions only. Every case also records its business
outcome: unsupported requests remain unfulfilled and rejected approvals remain
cancelled. Guardrail success must not be reported as support for those capabilities.


## Monitoring health and durable delivery

The price-alert page shows scan heartbeat, last attempted/successful check, consecutive data/check failures, disabled/expired rules, daily trigger quotas and delivery states per channel. The assistant reads the same durable records through `get_monitoring_health`. Closed sessions and cooldowns are waiting states, not successful checks or data failures. Daily quotas count triggers (0 is unlimited), not AI spending.

New hits, inbox events and destination outbox records commit together. An independent worker polls every 10 seconds with 90-second leases and a 45-second send timeout. Failures back off exponentially from 30 seconds, up to five attempts; the hit-history UI allows a confirmed manual retry. Successful destinations are not resent when another fails. Once-only rules still deliver their committed events after becoming disabled. Removed/disabled channels remain visible and can be retried after restoring the original channel. Missing enabled default channels retain the inbox notice and ask users to configure future alerts.

The event ID remains stable across retries. Without provider idempotency, a crash after acceptance but before acknowledgement can cause a duplicate: delivery is at least once. Provider acceptance does not prove recipient read. Legacy hits without channel receipts show an unknown outcome and are not automatically resent on upgrade. The outbox stores no channel credentials; public errors are fixed safe codes.

Health currently covers price alerts, not other automation, ongoing research tasks or AI spend budgets. The expected scan interval is 60 seconds plus up to 20 seconds of scheduler jitter; prolonged delays prompt a service check.
