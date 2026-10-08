# Assistant capability foundations

[简体中文](assistant-capabilities.zh-CN.md)

The assistant preserves source time separately from retrieval time. Quotes include
provider, source timestamp when verified, trading date, market status and freshness.
Unknown timestamps and closed or stale quotes cannot establish a live price; daily
changes remain available as source fields instead of being presented as today's move.

`check_watch_request` reads the original user turn once and returns explicit
instruments, portfolio/watchlist scope, a supported relative horizon, requested
condition types/channels, and capability gaps. Relative expiry is anchored to task
creation and retained through approval. This is a bounded parser, not a complete
natural-language guarantee: ambiguous names require stock lookup, and unsupported
or unrecognized requirements need clarification. Bar-close, moving-average,
consecutive-session, crossing and news-release triggers are not implemented.

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
a real SSE client disconnect and ordered replay. New synthetic conversations and
one alert are retained for inspection; do not run it over a human's acceptance data.
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
