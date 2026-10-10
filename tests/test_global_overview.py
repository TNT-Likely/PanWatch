"""Offline parser and contract tests; no personal DB or live network required."""
import asyncio
import unittest
import time
from unittest.mock import patch

from src.platform.marketdata.collectors import global_overview as g


class TestGlobalOverview(unittest.TestCase):
    def setUp(self):
        g.clear_overview_cache()

    def chart(self, **meta):
        return {"chart": {"result": [{"meta": {"symbol": "^GSPC", "instrumentType": "INDEX", "regularMarketPrice": 110,
            "regularMarketTime": 1791576000, "chartPreviousClose": 50, **meta},
            "timestamp": [1791489600, 1791576000],
            "indicators": {"quote": [{"close": [100, 110]}]}}]}}

    def test_catalog_and_asset_identity(self):
        self.assertEqual(len(g._SPECS), 20)
        self.assertEqual(len({s[0] for s in g._SPECS}), 20)
        self.assertEqual([s[2] for s in g._SPECS if s[1] == "commodities"], ["GC=F", "CL=F", "ZC=F"])
        self.assertNotIn("HG=F", [s[2] for s in g._SPECS])

    def test_chart_uses_previous_daily_bar_not_range_baseline(self):
        with patch.object(g, '_get', return_value=self.chart()):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['prev_close'], 100)
        self.assertAlmostEqual(row['change_pct'], 10)
        self.assertEqual(row['spark'], [100, 110])

    def test_one_point_chart_uses_explicit_daily_change(self):
        data = self.chart(fulldayPrice=110, fulldayChange=2)
        data['chart']['result'][0]['timestamp'] = [1791576000]
        data['chart']['result'][0]['indicators']['quote'][0]['close'] = [110]
        with patch.object(g, '_get', return_value=data):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['prev_close'], 108)
        self.assertEqual(len(row['spark']), 1)

    def test_wrong_identity_and_bad_quote_fail_closed(self):
        for data in (None, {}, self.chart(symbol='SPY'), self.chart(regularMarketPrice=float('nan'))):
            with self.subTest(data=data), patch.object(g, '_get', return_value=data):
                row = g._yahoo(g._SPECS[5])
                self.assertEqual(row['status'], 'unavailable')
                self.assertIsNone(row['current_price'])

    def test_stale_data_is_retained_but_labeled(self):
        with patch.object(g, '_get', return_value=self.chart(regularMarketTime=1000000)):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['status'], 'stale')
        self.assertEqual(row['current_price'], 110)

    def test_yields_sort_and_skip_missing_without_zeros(self):
        rows = g._yield_rows('US', 'US Treasury', ('A', 'B', 'C'))
        out = g._apply_yields(rows, [('2026-01-06', {'A': '4.03', 'B': '.'}),
                                   ('2026-01-05', {'A': '4.00'}), ('2026-01-04', {'A': 'bad'})])
        self.assertEqual(out[0]['change_bps'], 3)
        self.assertIsNone(out[0]['change_pct'])
        self.assertEqual(out[0]['spark'], [4, 4.03])
        self.assertIsNone(out[1]['current_price'])
        self.assertEqual(out[1]['status'], 'unavailable')
        self.assertEqual([r['tenor'] for r in out], ['3m', '5y', '10y'])

    def test_china_ignores_comment_columns_and_non_government_rows(self):
        cells = '<td>ChinaBond Government Bond Yield Curve</td><td>2026-10-09</td>'
        cells += '<!-- <td>wrong overnight column</td> -->'
        cells += ''.join(f'<td>{v}</td>' for v in ['1.1','1.2','1.3','1.4','1.5','1.6','1.7','1.8'])
        with patch.object(g, '_get', return_value=f'<table><tr>{cells}</tr></table>'):
            out = g._china_yields()
        self.assertEqual([r['current_price'] for r in out], [1.1, 1.5, 1.7])
        self.assertTrue(all(r['change_bps'] is None for r in out))

    def test_canada_actual_series_and_sort(self):
        symbols = ('TB.CDN.90D.MID', 'BD.CDN.5YR.DQ.YLD', 'BD.CDN.10YR.DQ.YLD')
        data = {'observations': [{'d': date, **{s: {'v': value} for s in symbols}}
                                for date, value in [('2026-10-09', '3.5'), ('2026-10-08', '3.4')]]}
        with patch.object(g, '_get', return_value=data) as fetch:
            out = g._canada_yields()
        self.assertIn(','.join(symbols), fetch.call_args[0][0])
        self.assertEqual(out[0]['change_bps'], 10)
        self.assertEqual(out[0]['spark'], [3.4, 3.5])

    def test_all_29_rows_survive_provider_outage_and_cache(self):
        with patch.object(g, '_get', return_value=None) as fetch:
            out = asyncio.run(g.get_global_overview())
            count = fetch.call_count
            self.assertEqual(asyncio.run(g.get_global_overview()), out)
            self.assertEqual(fetch.call_count, count)
        self.assertEqual(len(out), 29)
        self.assertEqual(len({r['group'] for r in out}), 9)
        self.assertTrue(all(r['current_price'] is None for r in out))

    def test_concurrent_requests_share_refresh_and_cancellation_isolated(self):
        async def scenario():
            gate = asyncio.Event()
            calls = []
            async def refresh():
                calls.append(True)
                await gate.wait()
                return [{"id": "test"}]
            with patch.object(g, '_load_global_overview', side_effect=refresh):
                first = asyncio.create_task(g.get_global_overview())
                second = asyncio.create_task(g.get_global_overview())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                gate.set()
                self.assertEqual(await second, [{"id": "test"}])
                self.assertEqual(len(calls), 1)
        asyncio.run(scenario())

    def test_unexpected_provider_error_keeps_asset_metadata(self):
        with patch.object(g, '_yahoo', side_effect=RuntimeError('offline')), patch.object(g, '_get', return_value=None):
            rows = asyncio.run(g.get_global_overview())
        by_id = {row['id']: row for row in rows}
        self.assertEqual(by_id['gold']['instrument_type'], 'futures')
        self.assertEqual(by_id['gold']['unit'], 'USD/oz')
        self.assertEqual(by_id['btc']['instrument_type'], 'crypto')
        self.assertEqual(by_id['btc']['unit'], 'USD')

    def test_wrong_instrument_type_is_rejected(self):
        with patch.object(g, '_get', return_value=self.chart(instrumentType='ETF')):
            self.assertIsNone(g._yahoo(g._SPECS[5])['current_price'])

    def test_aggregate_budget_returns_partial_rows_without_starting_queued_jobs(self):
        calls = []
        def slow(spec):
            calls.append(spec[0])
            if spec[0] != 'csi300':
                time.sleep(0.08)
            row = g._yahoo_base(spec)
            row['current_price'] = 100
            return row
        async def scenario():
            start = time.monotonic()
            rows = await g.get_global_overview()
            self.assertLess(time.monotonic() - start, 0.07)
            return rows
        with patch.object(g, '_yahoo', side_effect=slow), patch.object(g, '_REFRESH_TIMEOUT_S', 0.02), patch.object(g, '_get', return_value=None):
            rows = asyncio.run(scenario())
        self.assertEqual(len(rows), 29)
        self.assertEqual(rows[0]['current_price'], 100)
        self.assertIsNone(rows[-1]['current_price'])
        self.assertLessEqual(len(calls), 5)

    def test_missing_history_does_not_erase_valid_quote(self):
        data = self.chart(fulldayPrice=110, fulldayChange=2)
        del data['chart']['result'][0]['indicators']
        with patch.object(g, '_get', return_value=data):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['current_price'], 110)
        self.assertEqual(row['spark'], [])
        self.assertEqual(row['prev_close'], 108)

    def test_history_ignores_invalid_and_future_bars(self):
        data = self.chart()
        data['chart']['result'][0]['timestamp'] = [1791576000, None, 1791489600, 9999999999]
        data['chart']['result'][0]['indicators']['quote'][0]['close'] = [110, 7, 100, 9999]
        with patch.object(g, '_get', return_value=data):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['spark'], [100, 110])
        self.assertEqual(row['prev_close'], 100)

    def test_explicit_change_wins_over_gapped_history(self):
        data = self.chart(regularMarketChange=2)
        data['chart']['result'][0]['timestamp'] = [1791403200, 1791489600, 1791576000]
        data['chart']['result'][0]['indicators']['quote'][0]['close'] = [100, None, 110]
        with patch.object(g, '_get', return_value=data):
            row = g._yahoo(g._SPECS[5])
        self.assertEqual(row['prev_close'], 108)
        self.assertAlmostEqual(row['change_pct'], (110/108-1)*100)

    def test_invalid_immediate_prior_bar_does_not_use_older_close(self):
        for bad in (None, 0, -1, float('nan')):
            data = self.chart()
            data['chart']['result'][0]['timestamp'] = [1791403200, 1791489600, 1791576000]
            data['chart']['result'][0]['indicators']['quote'][0]['close'] = [100, bad, 110]
            with self.subTest(bad=bad), patch.object(g, '_get', return_value=data):
                row = g._yahoo(g._SPECS[5])
                self.assertIsNone(row['prev_close'])
                self.assertIsNone(row['change_pct'])
                self.assertEqual(row['spark'], [100, 110])


if __name__ == '__main__':
    unittest.main()
