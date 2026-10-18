"""Offline session regressions using daily/hourly Yahoo captures from 2026-10-10.

The hourly fixture is the unchanged response from
https://query2.finance.yahoo.com/v8/finance/chart/ZC%3DF?range=5d&interval=1h
SHA256: 0868fcca1d7cc4eb199efa7d2ad3d55bda08457fcfcc193383e02053e3fefe82.
OHLC observations are captured; intraday quote metadata and missing/lagging
change fields below are controlled replays, not captured live responses.
"""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

from src.platform.marketdata.collectors import global_overview as g


FIXTURES = Path(__file__).parent / 'fixtures'
DAILY = json.loads((FIXTURES / 'yahoo_corn_2026_10_09.json').read_text())
HOURLY = json.loads((FIXTURES / 'yahoo_corn_2026_10_05_09_hourly.json').read_text())
NY = ZoneInfo('America/New_York')
CORN_SPEC = next(spec for spec in g._SPECS if spec[0] == 'corn')
REAL_CLIENT = httpx.Client


def observations(payload):
    result = payload['chart']['result'][0]
    quote = result['indicators']['quote'][0]
    return [(datetime.fromtimestamp(ts, NY), {key: values[i] for key, values in quote.items()})
            for i, ts in enumerate(result['timestamp']) if quote['open'][i] is not None]


DAY_BARS = dict(observations(DAILY))
HOUR_BARS = observations(HOURLY)


def aggregate(bars):
    return dict(open=bars[0]['open'], high=max(bar['high'] for bar in bars),
                low=min(bar['low'] for bar in bars), close=bars[-1]['close'])


def replay_payload(hour, session_day, **changes):
    """Use an observed hourly close at a synthetic end-of-hour quote time.

    session_day is known to the test from captured OHLC aggregation. It is
    deliberately not passed to the collector as invented provider metadata.
    No completed future daily value is retained in the replay.
    """
    at = datetime.fromisoformat(hour).replace(tzinfo=NY)
    price = dict(HOUR_BARS)[at]['close']
    quote_at = at + timedelta(minutes=59, seconds=59)
    session = datetime.fromisoformat(session_day).replace(tzinfo=NY)
    payload = copy.deepcopy(DAILY)
    result = payload['chart']['result'][0]
    result['meta'] = {key: result['meta'][key] for key in
                      ('symbol', 'instrumentType', 'exchangeTimezoneName', 'currency', 'shortName')}
    result['meta'].update(regularMarketTime=quote_at.timestamp(), regularMarketPrice=price, **changes)
    completed = [(day.timestamp(), bar['close']) for day, bar in DAY_BARS.items() if day < session]
    # A midnight-labelled partial bar can be present after midnight, never
    # before its label. Its close is the observed quote, not the final day close.
    if session <= quote_at:
        completed.append((session.timestamp(), price))
    result['timestamp'] = [ts for ts, _ in completed]
    result['indicators'] = {'quote': [{'close': [value for _, value in completed]}]}
    return payload


class TestYahooFuturesSessions(unittest.TestCase):
    def replay(self, payload):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=payload, request=request)

        def client(**kwargs):
            return REAL_CLIENT(**dict(kwargs, transport=httpx.MockTransport(handle), trust_env=False))

        with patch('marketdata.http.httpx.Client', side_effect=client):
            row = g._yahoo(CORN_SPEC)
        self.assertEqual(len(requests), 1)
        self.assertEqual(dict(requests[0].url.params), {'range': '1mo', 'interval': '1d'})
        return row

    def assert_quote_preserved_without_change(self, payload):
        row = self.replay(payload)
        result = payload['chart']['result'][0]
        self.assertEqual(row['current_price'], result['meta']['regularMarketPrice'])
        self.assertEqual(row['as_of'], g._iso(result['meta']['regularMarketTime']))
        self.assertEqual(row['spark'], result['indicators']['quote'][0]['close'][-20:])
        self.assertEqual(row['history_as_of'], g._iso(result['timestamp'][-1]))
        self.assertIn(row['status'], ('delayed', 'stale'))
        self.assertEqual(row['timezone'], 'America/Chicago')
        for field in ('prev_close', 'change_amount', 'change_pct'):
            self.assertIsNone(row[field], field)
        return row

    def test_captured_daily_labels_include_previous_evening(self):
        for day in ('2026-10-07', '2026-10-09'):
            with self.subTest(day=day):
                midnight = datetime.fromisoformat(day).replace(tzinfo=NY)
                session = aggregate([bar for at, bar in HOUR_BARS
                                     if midnight - timedelta(hours=4) <= at < midnight + timedelta(hours=16)])
                calendar = aggregate([bar for at, bar in HOUR_BARS
                                      if midnight <= at < midnight + timedelta(days=1)])
                daily = {key: DAY_BARS[midnight][key] for key in ('open', 'high', 'low', 'close')}
                self.assertEqual(daily, session)
                self.assertNotEqual(daily, calendar)

    def test_overnight_missing_and_unusable_changes_do_not_reverse_direction(self):
        variants = [
            {},
            {'fulldayPrice': 500.25, 'fulldayChange': -1.75},
            {'regularMarketChange': None},
            {'regularMarketChange': 'bad'},
            {'regularMarketChange': 'NaN'},
            {'regularMarketChange': 'Infinity'},
            {'regularMarketChange': '-Infinity', 'fulldayPrice': 500.50, 'fulldayChange': 'NaN'},
            {'regularMarketChange': 'bad', 'fulldayPrice': 500.50, 'fulldayChange': None},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                payload = replay_payload('2026-10-08T20:00', '2026-10-09', **changes)
                row = self.assert_quote_preserved_without_change(payload)
                self.assertEqual(row['current_price'], 500.50)
                self.assertEqual(row['spark'][-2:], [502, 500.25])

    def test_captured_calendar_periods_do_not_verify_the_quote_session(self):
        meta = HOURLY['chart']['result'][0]['meta']
        payload = replay_payload('2026-10-08T20:00', '2026-10-09')
        quote_at = payload['chart']['result'][0]['meta']['regularMarketTime']
        period = next(period for day in meta['tradingPeriods'] for period in day
                      if period['start'] <= quote_at < period['end'])
        # This real provider period contains the next session's evening quote,
        # yet both of its bounds are on the previous calendar day.
        self.assertEqual(datetime.fromtimestamp(period['start'], NY).isoformat(), '2026-10-08T00:00:00-04:00')
        self.assertEqual(datetime.fromtimestamp(period['end'], NY).isoformat(), '2026-10-08T23:59:00-04:00')
        for current in (meta['currentTradingPeriod'], {'regular': period}, None, {}, 'bad'):
            with self.subTest(current=current):
                result = payload['chart']['result'][0]
                result['meta'].update(currentTradingPeriod=current, tradingPeriods=meta['tradingPeriods'])
                self.assert_quote_preserved_without_change(payload)

    def test_day_evening_and_midnight_boundaries_keep_explicit_changes(self):
        cases = [
            ('2026-10-08T12:00', '2026-10-08', 502.00),
            ('2026-10-08T20:00', '2026-10-09', 500.25),
            ('2026-10-08T23:00', '2026-10-09', 500.25),
            ('2026-10-09T00:00', '2026-10-09', 500.25),
            ('2026-10-09T12:00', '2026-10-09', 500.25),
        ]
        for hour, session, prior in cases:
            payload = replay_payload(hour, session)
            price = payload['chart']['result'][0]['meta']['regularMarketPrice']
            with self.subTest(hour=hour, change='absent'):
                self.assert_quote_preserved_without_change(payload)
            for changes in ({'regularMarketChange': price - prior},
                            {'fulldayPrice': price, 'fulldayChange': price - prior},
                            {'regularMarketChange': 'NaN', 'fulldayPrice': price, 'fulldayChange': price - prior}):
                with self.subTest(hour=hour, changes=changes):
                    row = self.replay(replay_payload(hour, session, **changes))
                    self.assertEqual(row['prev_close'], prior)
                    self.assertEqual(row['change_amount'], price - prior)
                    self.assertAlmostEqual(row['change_pct'], (price / prior - 1) * 100)


if __name__ == '__main__':
    unittest.main()
