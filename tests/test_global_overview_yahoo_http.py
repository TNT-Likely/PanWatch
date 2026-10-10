"""Deterministic Yahoo replay through the production HTTP and API boundaries.

The corn fixture was captured from the public chart endpoint on 2026-10-10.
Missing/lagging change fields and all other payloads are controlled variants;
these tests never make live provider requests.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.market.api.market import router
from src.platform.marketdata.collectors import global_overview as g


CORN = json.loads((Path(__file__).parent / 'fixtures' / 'yahoo_corn_2026_10_09.json').read_text())
CORN_SPEC = next(spec for spec in g._SPECS if spec[0] == 'corn')
REAL_CLIENT = httpx.Client
# Provider timezone names observed for the complete catalog on 2026-10-10.
PROVIDER_ZONES = {
    'csi300': 'Asia/Shanghai', 'star50': 'Asia/Shanghai', 'chinext': 'Asia/Shanghai',
    'hsi': 'Asia/Hong_Kong', 'hstech': 'Asia/Hong_Kong',
    'sp500': 'America/New_York', 'nasdaq': 'America/New_York', 'dow': 'America/New_York',
    'tsx': 'America/Toronto', 'cac40': 'Europe/Paris', 'dax': 'Europe/Berlin',
    'ftse100': 'Europe/London', 'stoxx50': 'Europe/Zurich', 'nikkei225': 'Asia/Tokyo',
    'vnindex': 'Asia/Bangkok', 'gold': 'America/New_York', 'oil': 'America/New_York',
    'corn': 'America/New_York', 'btc': 'UTC', 'eth': 'UTC',
}


def epoch(date, zone, hour=0):
    return int(datetime.fromisoformat(date).replace(hour=hour, tzinfo=ZoneInfo(zone)).timestamp())


def chart(spec=CORN_SPEC, *, day='2026-10-09', prior='2026-10-08', **meta):
    zone = PROVIDER_ZONES[spec[0]]
    kind = g._yahoo_base(spec)['instrument_type']
    return {'chart': {'result': [{
        'meta': {
            'symbol': spec[2],
            'instrumentType': {'index': 'INDEX', 'futures': 'FUTURE', 'crypto': 'CRYPTOCURRENCY'}[kind],
            'exchangeTimezoneName': zone, 'regularMarketTime': epoch(day, zone, 16),
            'regularMarketPrice': 110, 'chartPreviousClose': 50,
            # This is the quote's offset only, not a replacement for a DST-aware zone.
            'gmtoffset': int(datetime.fromtimestamp(epoch(day, zone, 16), ZoneInfo(zone)).utcoffset().total_seconds()),
            **meta,
        },
        'timestamp': [epoch(prior, zone), epoch(day, zone)],
        'indicators': {'quote': [{'close': [100, 110]}]},
    }]}}


def transport_client(handler):
    def client(**kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        kwargs['trust_env'] = False
        return REAL_CLIENT(**kwargs)
    return client


class TestYahooHttpBoundary(unittest.TestCase):
    def setUp(self):
        g.clear_overview_cache()

    def replay(self, payload, spec=CORN_SPEC, now=None):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=payload, request=request)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (now or datetime(2026, 10, 10, 12, tzinfo=timezone.utc)).astimezone(tz)

        with patch('marketdata.http.httpx.Client', side_effect=transport_client(handle)), patch.object(g, 'datetime', Clock):
            row = g._yahoo(spec)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.url.host, 'query1.finance.yahoo.com')
        self.assertEqual(unquote(request.url.path.rsplit('/', 1)[1]), spec[2])
        self.assertEqual(dict(request.url.params), {'range': '1mo', 'interval': '1d'})
        self.assertEqual(row['timezone'], spec[3])
        return row

    def test_saved_corn_without_change_uses_previous_provider_day(self):
        payload = copy.deepcopy(CORN)
        meta = payload['chart']['result'][0]['meta']
        meta.pop('regularMarketChange', None)
        meta.pop('fulldayChange', None)
        row = self.replay(payload)
        self.assertEqual(row['current_price'], 479.75)
        self.assertEqual(row['prev_close'], 500.25)
        self.assertAlmostEqual(row['change_pct'], -4.097951024487756)
        self.assertEqual(row['timezone'], 'America/Chicago')
        self.assertEqual(row['history_as_of'], '2026-10-09T04:00:00+00:00')

    def test_saved_corn_lagging_fullday_uses_previous_provider_day(self):
        payload = copy.deepcopy(CORN)
        payload['chart']['result'][0]['meta']['regularMarketPrice'] = 480
        row = self.replay(payload)
        self.assertEqual(row['prev_close'], 500.25)
        self.assertAlmostEqual(row['change_amount'], -20.25)
        self.assertAlmostEqual(row['change_pct'], (480 / 500.25 - 1) * 100)

    def test_saved_corn_explicit_change_still_wins(self):
        payload = copy.deepcopy(CORN)
        payload['chart']['result'][0]['indicators']['quote'][0]['close'][-2] = 900
        row = self.replay(payload)
        self.assertEqual(row['prev_close'], 500.25)
        self.assertAlmostEqual(row['change_amount'], -20.5)

    def test_complete_catalog_uses_provider_calendar_across_weekends_and_dst(self):
        self.assertEqual(set(PROVIDER_ZONES), {spec[0] for spec in g._SPECS})
        dates = [
            ('2026-10-09', '2026-10-08'),
            ('2026-03-09', '2026-03-06'),  # North America spring DST weekend
            ('2026-03-30', '2026-03-27'),  # Europe spring DST weekend
            ('2026-10-26', '2026-10-23'),  # Europe fall DST weekend
            ('2026-11-02', '2026-10-30'),  # North America fall DST weekend
            ('2026-11-01', '2026-10-30'),  # Intraday DST transition: midnight has another offset
        ]
        for spec in g._SPECS:
            for day, prior in dates:
                with self.subTest(symbol=spec[2], day=day):
                    row = self.replay(chart(spec, day=day, prior=prior), spec)
                    self.assertEqual(row['current_price'], 110)
                    self.assertEqual(row['prev_close'], 100)
                    self.assertAlmostEqual(row['change_pct'], 10)
                    self.assertEqual(row['spark'], [100, 110])

    def test_stale_quote_uses_its_own_day_instead_of_today(self):
        for now, expected_status in [
            (datetime(2026, 10, 11, 12, tzinfo=timezone.utc), 'delayed'),
            (datetime(2026, 10, 20, 12, tzinfo=timezone.utc), 'stale'),
        ]:
            with self.subTest(now=now):
                row = self.replay(chart(), now=now)
                self.assertEqual(row['prev_close'], 100)
                self.assertEqual(row['status'], expected_status)

    def test_missing_current_bar_still_uses_latest_prior_session(self):
        payload = chart(day='2026-10-12', prior='2026-10-09')
        result = payload['chart']['result'][0]
        result['timestamp'].pop()
        result['indicators']['quote'][0]['close'].pop()
        row = self.replay(payload)
        self.assertEqual(row['prev_close'], 100)
        self.assertEqual(row['spark'], [100])

    def test_unknown_provider_zone_leaves_only_derived_change_unavailable(self):
        for zone in (None, '', 'Not/A_Zone', '/absolute/path', 0, [], {}):
            with self.subTest(zone=zone):
                payload = chart(exchangeTimezoneName=zone, timezone='EDT', gmtoffset=-14400)
                row = self.replay(payload)
                self.assertEqual(row['current_price'], 110)
                self.assertEqual(row['status'], 'delayed')
                self.assertEqual(row['spark'], [100, 110])
                self.assertIsNone(row['prev_close'])
                self.assertIsNone(row['change_pct'])
                self.assertIsNone(row['change_amount'])
        payload = chart()
        del payload['chart']['result'][0]['meta']['exchangeTimezoneName']
        self.assertIsNone(self.replay(payload)['prev_close'])

    def test_valid_explicit_change_does_not_need_timezone_or_history(self):
        changes = [
            {'regularMarketChange': 2, 'fulldayPrice': 110, 'fulldayChange': 3},
            {'regularMarketChange': None, 'fulldayPrice': 110, 'fulldayChange': 2},
            {'regularMarketChange': 'NaN', 'fulldayPrice': 110, 'fulldayChange': 2},
            {'regularMarketChange': 0},
        ]
        for change in changes:
            with self.subTest(change=change):
                payload = chart(exchangeTimezoneName='Not/A_Zone', **change)
                del payload['chart']['result'][0]['indicators']
                row = self.replay(payload)
                self.assertEqual(row['prev_close'], 110 if change.get('regularMarketChange') == 0 else 108)
                self.assertEqual(row['current_price'], 110)
                self.assertEqual(row['spark'], [])

    def test_bad_immediate_prior_bar_is_not_replaced_by_older_valid_close(self):
        for spec in (g._SPECS[5], CORN_SPEC):
            bad_closes = [None, 'bad', 'NaN'] if spec == CORN_SPEC else [None, 'bad', 'NaN', 0, -1]
            for bad_close in bad_closes:
                with self.subTest(symbol=spec[2], close=bad_close):
                    payload = chart(spec)
                    result = payload['chart']['result'][0]
                    result['timestamp'].insert(0, epoch('2026-10-07', PROVIDER_ZONES[spec[0]]))
                    result['indicators']['quote'][0]['close'] = [90, bad_close, 110]
                    row = self.replay(payload, spec)
                    self.assertEqual(row['current_price'], 110)
                    self.assertEqual(row['spark'], [90, 110])
                    self.assertIsNone(row['prev_close'])
                    self.assertIsNone(row['change_pct'])

    def test_empty_or_future_only_history_does_not_use_range_baseline(self):
        for bars in ([], [epoch('2026-10-10', 'America/New_York')], [epoch('2026-10-09', 'America/New_York')]):
            with self.subTest(bars=bars):
                payload = chart(fulldayPrice=109, fulldayChange=2)
                result = payload['chart']['result'][0]
                result['timestamp'] = bars
                result['indicators']['quote'][0]['close'] = [110] * len(bars)
                row = self.replay(payload)
                self.assertEqual(row['current_price'], 110)
                self.assertIsNone(row['prev_close'])
                self.assertIsNone(row['change_pct'])

    def test_bad_identity_and_quote_remain_unavailable_through_http(self):
        for spec in g._SPECS:
            for changed in ({'symbol': 'WRONG'}, {'instrumentType': 'ETF'}, {'regularMarketPrice': 'NaN'}, {'regularMarketTime': None}):
                with self.subTest(symbol=spec[2], changed=changed):
                    row = self.replay(chart(spec, **changed), spec)
                    self.assertIsNone(row['current_price'])
                    self.assertIsNone(row['prev_close'])
                    self.assertEqual(row['status'], 'unavailable')

    def test_real_api_serialization_keeps_all_display_zones_and_correct_changes(self):
        payloads = {spec[2]: chart(spec) for spec in g._SPECS}
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.host == 'query1.finance.yahoo.com':
                return httpx.Response(200, json=payloads[unquote(request.url.path.rsplit('/', 1)[1])])
            if request.url.host == 'home.treasury.gov':
                return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
            if request.url.host == 'yield.chinabond.com.cn':
                return httpx.Response(200, text='<table/>')
            self.assertEqual(request.url.host, 'www.bankofcanada.ca')
            return httpx.Response(200, json={})

        app = FastAPI()
        app.include_router(router, prefix='/api/market')
        with TestClient(app) as client:
            with patch('marketdata.http.httpx.Client', side_effect=transport_client(handle)):
                response = client.get('/api/market/overview')
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 29)
        self.assertEqual(len(requests), 24)
        by_id = {row['id']: row for row in rows}
        for spec in g._SPECS:
            with self.subTest(symbol=spec[2]):
                self.assertEqual(by_id[spec[0]]['timezone'], spec[3])
                self.assertEqual(by_id[spec[0]]['prev_close'], 100)
                self.assertAlmostEqual(by_id[spec[0]]['change_pct'], 10)
        self.assertTrue(all(row['status'] == 'unavailable' for row in rows if row['instrument_type'] == 'yield'))


if __name__ == '__main__':
    unittest.main()
