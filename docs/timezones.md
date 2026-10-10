# Timezone policy

[简体中文](timezones.zh-CN.md)

PanWatch separates four kinds of time. Interface language never selects a timezone.

| Kind | Policy | Example |
| --- | --- | --- |
| Stored instant | UTC; API timestamps include `Z` or an explicit offset | A rule expiring at `16:00+08:00` expires at `08:00Z` |
| User display and input | Browser timezone, detected with `Intl`; no shared display preference | A UTC browser shows and edits that expiry as `08:00` |
| Background wall clock | Deployment `TZ`, shared by schedules and notification quiet hours | `0 9 * * 1-5` means 09:00 in deployment `TZ` |
| Market calendar date | Exchange timezone, independent of browser and deployment | US trade dates use `America/New_York` |

## Display and editing

Settings shows the browser timezone and the background execution timezone separately.
Upcoming execution previews use browser time; schedule inputs remain explicitly labeled
with the execution timezone. Alert expiry inputs use browser time. An unchanged expiry
retains its exact instant, including seconds and a repeated DST hour. A newly entered
nonexistent spring DST time is rejected. A newly entered ambiguous fall DST time uses
the browser's earlier occurrence; unchanged persisted expiries preserve either occurrence.

The dashboard's “today” alert feed sends the browser IANA timezone. The backend builds
both local midnights and queries `[start, end)` in UTC, including 23- and 25-hour days.
Clients omitting the parameter retain the deployment day for compatibility.
Invalid explicit timezones return an error.

Both the alert list and stock-detail alert entry pass the original expiry to the shared
form. External API/agent expiry strings must include `Z` or an offset; unqualified
strings are rejected instead of silently assuming UTC. Internal naive UTC datetime
values remain compatible. Paper close dates are converted from instants to the viewer's
date; exchange/report dates retain their date-only meaning. Research metadata separates
the report date from the localized `generated_at` timestamp.

News publication timestamps carry an offset. Chinese source wall-clock strings from
EastMoney and Sina are interpreted in `Asia/Shanghai` and converted to UTC. Epoch
timestamps from Xueqiu and CLS already identify an instant and are unchanged.
Date-only exchange data, K-line dates, and report dates must not be parsed as user instants.

## Background execution

To run schedules and quiet hours in UTC, set `TZ=UTC` in the deployment environment and
restart the service. Use IANA names such as `America/New_York` when DST is required.
The existing default remains `Asia/Shanghai` so an upgrade cannot silently move existing
jobs. Review cron wall times before changing `TZ`; `09:00` in two zones is a different instant.
Browser navigation and language changes never rewrite existing jobs.

Docker supports `docker run -e TZ=UTC ...` and Compose `environment: { TZ: UTC }`.
The image includes timezone data and does not infer the host timezone. Recreate the
container after changing its environment; retain the data volume.

## Regression checks

`pnpm --dir frontend check:timezones` and the frontend build reject direct truncation
of known instant fields and literal non-UTC display zones. This is a syntactic guard,
not complete data-flow analysis. Exchange zones should come from the market mapping;
UTC formatting for a date-only weekday is intentional.

The timezone CI workflow tests the complete stock alert entry, unchanged microseconds,
local input, midnight boundaries and DST. Browser cases cover UTC, Shanghai, New York,
Kolkata, Kathmandu and Kiritimati; backend contracts also run under UTC, Shanghai and
New York deployment clocks. Add an entry test when introducing another time-editing
surface, and classify each field as an instant, wall clock or market date before use.

## Compatibility and remaining work

SQLite UTC timestamps lose their offset on read; assistant DTOs restore UTC at the API
boundary. Existing API timestamps with explicit offsets are valid and remain compatible.
Legacy naive news or vendor timestamps cannot be migrated safely without their source
semantics. This change does not rewrite historical records.

Per-user display overrides and per-job execution timezone editing require additional
product scope and migration. Research/strategy `date.today()` defaults should be audited
per market before migrating them; their dates are not user display preferences. This
change leaves those daily aggregation semantics unchanged.
