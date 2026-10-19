"""Deterministic regressions for AUDIT_REPORT A01–A09 and adjacent safety cases."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tw_stock_swing.analysis import analyze_bars
from tw_stock_swing.catalog import load_catalog
from tw_stock_swing.cli import main
from tw_stock_swing.formatting import format_analysis
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.providers import DataProviderError, YahooFinanceProvider
from tw_stock_swing.research import _coverage_items, _health, research_stock
from tw_stock_swing.sources import ConnectorError, OFFICIAL_CONNECTORS
from tw_stock_swing.sources.official import parse_number
from tw_stock_swing.storage import ResearchDatabase, _iso


WHEN = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)
FIXTURES = Path(__file__).with_name('fixtures')
TRADE_FIELDS = ('entry_low', 'entry_high', 'stop_loss', 'target_1', 'target_2', 'risk_reward_1')


def market(factor=1.0):
    days, cursor = [], date(2026, 10, 16)
    while len(days) < 90:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return MarketData('2330.TW', 'synthetic', tuple(
        PriceBar(day, 100, 101, 99, 100, 1000000, 100 * factor)
        for day in reversed(days)), 'audit fixture')


def fixture(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def parsed(name, payload=None):
    return OFFICIAL_CONNECTORS[name].parse(fixture(name) if payload is None else payload,
                                           symbol='2330', fetched_at=WHEN)


def yahoo_payload():
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': '2330.TW', 'currency': 'TWD', 'instrumentType': 'EQUITY'}, 'timestamp': [int(WHEN.timestamp())],
        'indicators': {'quote': [{'open': [100], 'high': [101], 'low': [99],
                                  'close': [100], 'volume': [1000]}]}}]}}


class PriceSafetyTests(unittest.TestCase):
    def test_a01_constant_adjustment_does_not_create_return(self):
        for factor in (1, 0.8, 0.5, 2):
            with self.subTest(factor=factor):
                self.assertEqual(analyze_bars(market(factor), as_of=WHEN).change_20d_pct, 0)

    def test_a01_split_series_has_no_false_return(self):
        data = market()
        older = tuple(replace(b, open=200, high=202, low=198, close=200, adjusted_close=100)
                      for b in data.bars[:-10])
        result = analyze_bars(replace(data, bars=older + data.bars[-10:]), as_of=WHEN)
        self.assertEqual(result.change_20d_pct, 0)

    def test_a01_cash_dividend_total_return_uses_same_basis(self):
        data = market()
        older = tuple(replace(b, adjusted_close=95) for b in data.bars[:-10])
        later = tuple(replace(b, open=95, high=96, low=94, close=95, adjusted_close=95)
                      for b in data.bars[-10:])
        self.assertEqual(analyze_bars(replace(data, bars=older + later), as_of=WHEN).change_20d_pct, 0)

    def test_a02_future_data_is_rejected_before_scoring(self):
        with self.assertRaisesRegex(ValueError, '截止時間'):
            analyze_bars(market(), as_of=WHEN - timedelta(days=14))

    def test_mixed_adjustment_bases_rejected(self):
        data = market()
        data = replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], adjusted_close=None)))
        with self.assertRaisesRegex(ValueError, '還原價格部分缺漏'):
            analyze_bars(data, as_of=WHEN)

    def test_invalid_volume_and_ohlc_rejected(self):
        data = market()
        for change in ({'volume': float('inf')}, {'volume': True}, {'volume': 1.5},
                       {'high': float('inf')}, {'close': float('nan')}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze_bars(replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], **change))), as_of=WHEN)


class OfficialSafetyTests(unittest.TestCase):
    def test_a03_all_nonfinite_forms_are_unavailable(self):
        for value in ('NaN', 'Infinity', '-inf', float('nan'), float('inf'), True):
            with self.subTest(value=value):
                self.assertIsNone(parse_number(value))
                payload = fixture('twse_market')
                payload[0]['ClosingPrice'] = value
                result = parsed('twse_market', payload)
                self.assertNotIn('market.close', {o.field_id for o in result.observations})
                self.assertNotEqual(next(c.status for c in result.coverage if c.field_id == 'market.close'), 'available')

    def test_a09_ohlc_contradiction_quarantines_all_prices(self):
        payload = fixture('twse_market')
        payload[0]['HighestPrice'] = '1'
        result = parsed('twse_market', payload)
        for field in ('open', 'high', 'low', 'close'):
            self.assertEqual(next(c.status for c in result.coverage if c.field_id == f'market.{field}'), 'conflicting')
            self.assertNotIn(f'market.{field}', {o.field_id for o in result.observations})

    def test_a09_negative_volume_and_fractional_trades_rejected(self):
        for field, value, target in (('TradeVolume', '-1', 'market.volume'), ('Transaction', '1.5', 'market.transactions')):
            payload = fixture('twse_market')
            payload[0][field] = value
            result = parsed('twse_market', payload)
            self.assertEqual(next(c.status for c in result.coverage if c.field_id == target), 'conflicting')

    def test_a09_institutional_arithmetic_checked(self):
        payload = fixture('twse_institutional')
        payload['data'][0][4] = '999'
        result = parsed('twse_institutional', payload)
        self.assertTrue(all(c.status == 'conflicting' for c in result.coverage
                            if c.field_id.startswith('institutional.foreign.')))

    def test_malformed_tables_and_duplicate_symbols_fail_loudly(self):
        for change in ('short_row', 'duplicate_header', 'non_list'):
            payload = fixture('twse_institutional')
            if change == 'short_row':
                payload['data'][0].pop()
            elif change == 'duplicate_header':
                payload['fields'][1] = payload['fields'][0]
            else:
                payload['data'].append('bad row')
            with self.subTest(change=change), self.assertRaises(ConnectorError):
                parsed('twse_institutional', payload)
        payload = fixture('twse_market')
        with self.assertRaises(ConnectorError):
            parsed('twse_market', payload * 2)

    def test_a04_partial_does_not_hide_stale_or_unknown_time(self):
        payload = fixture('twse_market')
        payload[0].pop('Transaction')
        result = parsed('twse_market', payload)
        health = _health(result, WHEN + timedelta(days=14))
        self.assertEqual((health.time_status, health.completeness_status), ('stale', 'partial'))
        coverage, _ = _coverage_items(load_catalog(), (result, parsed('twse_institutional'), parsed('twse_margin')),
                                      WHEN + timedelta(days=14))
        by_id = {c.field_id: c for c in coverage}
        self.assertEqual(by_id['market.close'].status, 'stale')
        self.assertEqual(by_id['market.transactions'].status, 'missing')
        unknown = _health(replace(result, valid_time_verified=False), WHEN)
        self.assertEqual((unknown.time_status, unknown.completeness_status), ('unverified_time', 'partial'))

    def test_intraday_today_official_daily_data_is_unverified(self):
        payload = fixture('twse_market')
        payload[0]['Date'] = '20261016'
        result = parsed('twse_market', payload)
        self.assertEqual(_health(result, WHEN - timedelta(hours=6)).time_status, 'future')


class StorageSafetyTests(unittest.TestCase):
    def test_a05_timezone_equivalence_and_naive_rejection(self):
        self.assertEqual(_iso('2026-10-16T10:00:00+08:00'), _iso('2026-10-16T02:00:00Z'))
        self.assertEqual(_iso('2026-10-16T00:30:00+08:00'), _iso('2026-10-15T16:30:00Z'))
        self.assertNotEqual(_iso('2026-11-01T01:30:00-04:00'), _iso('2026-11-01T01:30:00-05:00'))
        for value in ('bad', '2026-10-16', '2026-10-16T02:00:00', datetime(2026, 10, 16)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _iso(value)

    def test_a05_legacy_offsets_and_revisions_are_read_without_rewrite(self):
        with ResearchDatabase() as db:
            db.migrate()
            db.upsert_source('audit', 'audit', 'test')
            iid = db.add_instrument('2330', 'TWSE', 'audit', WHEN)
            arguments = dict(instrument_id=iid, field_id='market.close', unit='TWD',
                             valid_time='2026-10-16T00:00:00Z', known_at='2026-10-16T10:00:00+08:00',
                             system_recorded_at='2026-10-16T02:00:00Z', source_id='audit')
            first = db.add_observation(value=100, **arguments)
            # Simulate a database created before this fix; preserve original text.
            db.connection.execute('UPDATE observations SET known_at=?, valid_time=? WHERE observation_id=?',
                                  ('2026-10-16T10:00:00+08:00', '2026-10-16T08:00:00+08:00', first))
            rows = db.observations_as_of(iid, '2026-10-16T03:00:00Z')
            self.assertEqual([r.value_number for r in rows], [100])
            db.add_observation(value=101, **arguments)
            rows = db.observations_as_of(iid, '2026-10-16T03:00:00Z')
            self.assertEqual([(r.value_number, r.revision) for r in rows], [(101, 2)])
            original = db.connection.execute('SELECT known_at FROM observations WHERE observation_id=?', (first,)).fetchone()[0]
            self.assertEqual(original, '2026-10-16T10:00:00+08:00')
            for value in (float('nan'), float('inf'), True):
                with self.assertRaises(ValueError):
                    db.add_observation(value=value, **arguments)


class ProviderSafetyTests(unittest.TestCase):
    def test_a08_mismatched_missing_or_wrong_market_symbol_rejected(self):
        for symbol in ('2317.TW', '2330.TWO', ''):
            payload = yahoo_payload()
            payload['chart']['result'][0]['meta']['symbol'] = symbol
            with self.subTest(symbol=symbol), self.assertRaisesRegex(DataProviderError, '身分不一致'):
                YahooFinanceProvider()._parse(payload, '2330.TW')

    def test_skipped_rows_are_visible_and_block_analysis(self):
        payload = yahoo_payload()
        result = payload['chart']['result'][0]
        result['timestamp'].append(int(WHEN.timestamp()) + 86400)
        data = YahooFinanceProvider()._parse(payload, '2330.TW')
        self.assertTrue(data.quality_issues)
        with self.assertRaisesRegex(ValueError, '日線品質'):
            analyze_bars(data, as_of=WHEN)

    def test_invalid_volume_is_structured_error(self):
        for value in (float('inf'), 'NaN', -1, 1.5, True):
            payload = yahoo_payload()
            payload['chart']['result'][0]['indicators']['quote'][0]['volume'] = [value]
            with self.subTest(value=value), self.assertRaises(DataProviderError):
                YahooFinanceProvider()._parse(payload, '2330.TW')

    def test_malformed_nested_payload_is_structured_error(self):
        for payload in ({'chart': []}, {'chart': {'result': [None]}}, {'chart': {'result': 'bad'}}):
            with self.subTest(payload=payload), self.assertRaises(DataProviderError):
                YahooFinanceProvider()._parse(payload, '2330.TW')


class OutputSafetyTests(unittest.TestCase):
    def cli(self, arguments, outcomes):
        output = StringIO()
        with patch('tw_stock_swing.cli._analyze_one', side_effect=outcomes), redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def test_a06_partial_failures_cannot_report_success(self):
        result = analyze_bars(market(), as_of=WHEN)
        code, payload = self.cli(['scan', '2330', '2317', '--workers', '1', '--json'],
                                 [result, DataProviderError('outage')])
        self.assertEqual((code, payload['status']), (3, 'partial'))
        self.assertEqual((payload['requested'], payload['succeeded']), (2, 1))
        self.assertIn('2317', payload['errors'])

    def test_scan_all_failure_all_stale_and_success(self):
        current = analyze_bars(market(), as_of=WHEN)
        for outcome, expected in ((DataProviderError('outage'), (2, 'error')),
                                  (replace(current, freshness_status='stale'), (3, 'partial')),
                                  (current, (0, 'ok'))):
            code, payload = self.cli(['scan', '2330', '--json'], [outcome])
            self.assertEqual((code, payload['status']), expected)

    def test_a07_analyze_scan_and_text_hide_trade_plan(self):
        result = analyze_bars(market(), as_of=WHEN)
        for command in ('analyze', 'scan'):
            code, payload = self.cli([command, '2330', '--json'], [result])
            self.assertEqual(code, 0)
            self.assertEqual(payload['scope'], 'technical_screen_only')
            self.assertFalse(payload['decision_eligible'])
            analysis = payload['analysis'] if command == 'analyze' else payload['results'][0]
            self.assertTrue(all(analysis[key] is None for key in TRADE_FIELDS))
        self.assertNotIn('參考價位：入場', format_analysis(result))
        self.assertIn('未通過完整研究決策閘門', format_analysis(result))
        self.assertTrue(all(result.to_dict()[key] is None for key in TRADE_FIELDS))
        self.assertTrue(all(result.to_dict(decision_eligible=True)[key] is not None for key in TRADE_FIELDS))
        stale = replace(result, freshness_status='stale')
        self.assertTrue(all(stale.to_dict(decision_eligible=True)[key] is None for key in TRADE_FIELDS))

    def test_standalone_p0_field_blocks_research(self):
        # All other contract requirements are passing to isolate the new leaf.
        import tw_stock_swing.research as module
        from tw_stock_swing.research import CoverageItem
        catalog = load_catalog()
        extra = replace(catalog.fields[-1], id='audit.required', category='technical')
        catalog = replace(catalog, fields=tuple(replace(f, implementation='implemented') for f in catalog.fields) + (extra,))
        coverage = tuple(CoverageItem(f.id, f.name, f.category, f.priority, f.implementation,
                          'missing' if f.id == extra.id else 'available', 'fixture', 'computed', WHEN)
                         for f in catalog.fields)
        class Provider:
            def fetch(self, symbol):
                return market()
        class Connector:
            def __init__(self, name):
                self.result = parsed(name)
            def fetch_safe(self, *args, **kwargs):
                return self.result
        connectors = {name: Connector(name) for name in module.ACTIVE_CONNECTORS['TWSE']}
        with patch.object(module, '_coverage_items', return_value=(coverage, {})), \
             patch.object(module, '_market_conflicts', return_value=()), \
             patch.object(module, '_health', side_effect=lambda r, when: replace(_health(r, when), status='ok')):
            report = research_stock('2330', provider=Provider(), connectors=connectors,
                                    catalog=catalog, generated_at=WHEN)
        self.assertFalse(report.decision_eligible)
        self.assertTrue(any('audit.required' in reason for reason in report.decision_blockers))


if __name__ == '__main__':
    unittest.main()
