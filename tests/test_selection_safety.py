"""Selection audit S01–S09 regressions; fixtures are not exchange calendars."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tw_stock_swing.analysis import analyze_bars
from tw_stock_swing.cli import main
from tw_stock_swing.formatting import format_analysis
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.providers import DataProviderError, YahooFinanceProvider
from tw_stock_swing.sources import OFFICIAL_CONNECTORS
from tw_stock_swing.research import research_stock
from tw_stock_swing.storage import ResearchDatabase

WHEN = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def market(count=100, volume=1_000_000, symbol='2330.TW'):
    days, cursor = [], date(2026, 10, 16)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    bars = tuple(PriceBar(day, 99.8+i, 100.1+i, 99.7+i, 100+i, volume, 100+i)
                 for i, day in enumerate(reversed(days)))
    return MarketData(symbol, '合成測試', bars, 'synthetic')


def payload(data):
    return {'chart': {'result': [{'meta': {'symbol': data.symbol, 'currency': 'TWD', 'instrumentType': 'EQUITY'},
        'timestamp': [int(datetime.combine(b.date, datetime.min.time(), timezone.utc).timestamp()) for b in data.bars],
        'indicators': {'quote': [{key: [getattr(b, key) for b in data.bars]
                                for key in ('open', 'high', 'low', 'close', 'volume')}],
                       'adjclose': [{'adjclose': [b.adjusted_close for b in data.bars]}]}}]}}


def cli(arguments, results):
    output = StringIO()
    with patch('tw_stock_swing.cli._analyze_one', side_effect=results), redirect_stdout(output):
        code = main(arguments)
    return code, json.loads(output.getvalue())


class SelectionSafetyTests(unittest.TestCase):
    def test_low_liquidity_is_not_a_candidate(self):
        result = analyze_bars(market(volume=1), as_of=WHEN)
        self.assertFalse(result.screening_eligible)
        self.assertEqual(result.verdict, '不納入')
        self.assertIn('low_median_turnover_proxy', result.screening_blockers)
        self.assertIsNone(result.to_dict(decision_eligible=True)['entry_low'])

    def test_zero_volume_is_blocked_even_with_other_liquid_days(self):
        data = market()
        data = replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], volume=0)))
        result = analyze_bars(data, as_of=WHEN)
        self.assertIn('zero_volume_in_recent_window', result.screening_blockers)
        self.assertFalse(result.screening_eligible)

    def test_liquidity_proxy_definition_and_threshold_boundary(self):
        data = market()
        result = analyze_bars(data, as_of=WHEN)
        self.assertEqual(result.median_turnover_proxy_20, 189_500_000)
        self.assertTrue(analyze_bars(data, as_of=WHEN, min_median_turnover_proxy=189_500_000).screening_eligible)
        self.assertFalse(analyze_bars(data, as_of=WHEN, min_median_turnover_proxy=189_500_001).screening_eligible)

    def test_invalid_liquidity_threshold_is_rejected(self):
        for threshold in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                analyze_bars(market(), as_of=WHEN, min_median_turnover_proxy=threshold)

    def test_down_day_heavy_volume_gets_no_bonus(self):
        data = market()
        close = data.bars[-2].close * .99
        last = replace(data.bars[-1], open=198, high=198.1, low=close-.1, close=close, adjusted_close=close)
        normal = analyze_bars(replace(data, bars=(*data.bars[:-1], last)), as_of=WHEN)
        heavy = analyze_bars(replace(data, bars=(*data.bars[:-1], replace(last, volume=10_000_000))), as_of=WHEN)
        self.assertEqual(normal.score, heavy.score)
        self.assertTrue(any('不作看多加分' in reason for reason in heavy.risks))

    def test_up_day_heavy_turnover_requires_strong_close(self):
        data = market()
        normal = analyze_bars(data, as_of=WHEN)
        heavy = analyze_bars(replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], volume=2_000_000))), as_of=WHEN)
        self.assertEqual(heavy.score, normal.score + 10)
        weak = replace(data.bars[-1], open=198.8, high=202, low=198.7)
        weak_normal = analyze_bars(replace(data, bars=(*data.bars[:-1], weak)), as_of=WHEN)
        weak_heavy = analyze_bars(replace(data, bars=(*data.bars[:-1], replace(weak, volume=2_000_000))), as_of=WHEN)
        self.assertEqual(weak_normal.score, weak_heavy.score)

    def test_missing_entire_sessions_block_ranking_without_claiming_holiday_knowledge(self):
        data = market(count=200)
        sparse = replace(data, bars=data.bars[1::2])
        parsed = YahooFinanceProvider()._parse(payload(sparse), sparse.symbol)
        result = analyze_bars(parsed, as_of=WHEN)
        self.assertFalse(result.screening_eligible)
        self.assertIn('unverified_session_gaps', result.screening_blockers)
        self.assertFalse(result.session_calendar_verified)
        self.assertEqual(result.return_window_observations, 20)
        self.assertIn('20 筆觀察間隔', format_analysis(result))

    def test_weekend_gaps_do_not_pretend_to_be_missing_weekdays(self):
        result = analyze_bars(market(), as_of=WHEN)
        self.assertEqual(result.unverified_weekday_gaps, ())
        self.assertFalse(result.session_calendar_verified)

    def test_unknown_weekday_holiday_is_conservatively_excluded(self):
        data = market()
        result = analyze_bars(replace(data, bars=data.bars[:50]+data.bars[51:]), as_of=WHEN)
        self.assertIn(data.bars[50].date.isoformat(), result.unverified_weekday_gaps)
        self.assertFalse(result.screening_eligible)

    def test_absent_empty_or_null_adjusted_series_is_rejected(self):
        for variant in ('absent', 'empty', 'null'):
            data = market()
            raw = payload(data)
            indicators = raw['chart']['result'][0]['indicators']
            if variant == 'absent':
                del indicators['adjclose']
            else:
                indicators['adjclose'][0]['adjclose'] = [] if variant == 'empty' else [None]*len(data.bars)
            parsed = YahooFinanceProvider()._parse(raw, data.symbol)
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, '還原價格全部缺漏'):
                analyze_bars(parsed, as_of=WHEN)

    def test_split_does_not_create_volume_bonus(self):
        data = market()
        bars = tuple(replace(b, open=100, high=100, low=100, close=100, volume=1_000_000, adjusted_close=50)
                     for b in data.bars[:-1])
        last = replace(data.bars[-1], open=50, high=50, low=50, close=50, adjusted_close=50)
        unchanged = analyze_bars(replace(data, bars=(*bars, replace(last, volume=1_000_000))), as_of=WHEN)
        doubled = analyze_bars(replace(data, bars=(*bars, replace(last, volume=2_000_000))), as_of=WHEN)
        self.assertFalse(doubled.volume_signal_usable)
        self.assertEqual(doubled.turnover_proxy_ratio, 1)
        self.assertEqual(doubled.change_20d_pct, 0)
        self.assertEqual(unchanged.score, doubled.score)
        self.assertFalse(any('突破前' in reason for reason in doubled.reasons))

    def test_adjustment_change_disables_volume_signal_conservatively(self):
        data = market()
        earlier = tuple(replace(b, adjusted_close=b.close*.95) for b in data.bars[:-1])
        result = analyze_bars(replace(data, bars=(*earlier, replace(data.bars[-1], volume=10_000_000))), as_of=WHEN)
        self.assertFalse(result.volume_signal_usable)

    def test_duplicate_alias_uses_one_rank(self):
        result = analyze_bars(market(), as_of=WHEN)
        code, output = cli(['scan', '2330', '2330.TW', '--workers', '1', '--json'], [result, result])
        self.assertEqual(code, 0)
        self.assertEqual(len(output['results']), 1)
        self.assertEqual(output['succeeded'], 2)
        self.assertEqual(output['unique_succeeded'], 1)
        self.assertEqual(output['duplicates'], [{'requested': '2330.TW', 'resolved': '2330.TW'}])

    def test_conflicting_alias_snapshots_are_excluded(self):
        result = analyze_bars(market(), as_of=WHEN)
        code, output = cli(['scan', '2330', '2330.TW', '--workers', '1', '--json'], [result, replace(result, close=200)])
        self.assertEqual(code, 3)
        self.assertEqual(output['results'], [])
        self.assertIn('conflicting_alias_results', output['excluded_screening'][0]['blockers'])

    def test_scan_excludes_illiquid_candidate(self):
        low = analyze_bars(market(volume=1), as_of=WHEN)
        liquid = analyze_bars(market(symbol='2317.TW'), as_of=WHEN)
        code, output = cli(['scan', '2330', '2317', '--workers', '1', '--json'], [low, liquid])
        self.assertEqual(code, 3)
        self.assertEqual([r['symbol'] for r in output['results']], ['2317.TW'])
        self.assertEqual(output['excluded_screening'][0]['symbol'], '2330.TW')

    def test_analyze_excluded_result_returns_insufficient(self):
        result = analyze_bars(market(volume=1), as_of=WHEN)
        code, output = cli(['analyze', '2330', '--json'], [result])
        self.assertEqual((code, output['status']), (4, 'insufficient'))
        self.assertIsNone(output['analysis']['entry_low'])

    def test_equal_score_not_sorted_by_unverified_share_volume(self):
        result = analyze_bars(market(), as_of=WHEN)
        other = replace(result, symbol='2317.TW', volume_ratio=0.1)
        code, output = cli(['scan', '2330', '2317', '--workers', '1', '--json'], [replace(result, volume_ratio=100), other])
        self.assertEqual(code, 0)
        self.assertEqual([r['symbol'] for r in output['results']], ['2317.TW', '2330.TW'])

    def test_currency_and_type_must_be_explicitly_supported(self):
        for updates in ({'currency': 'USD'}, {'instrumentType': 'ETF'}, {'currency': None}, {'instrumentType': None}):
            raw = payload(market())
            raw['chart']['result'][0]['meta'].update(updates)
            with self.subTest(updates=updates), self.assertRaisesRegex(DataProviderError, '幣別／證券類型'):
                YahooFinanceProvider()._parse(raw, '2330.TW')

    def test_zero_positive_daily_total_contradictions_quarantine_all_totals(self):
        row = {'Date': '20261016', 'Code': '2330', 'Name': '合成', 'OpeningPrice': '100',
               'HighestPrice': '100', 'LowestPrice': '100', 'ClosingPrice': '100', 'Change': '0',
               'TradeVolume': '1000000', 'TradeValue': '100000000', 'Transaction': '1000'}
        for key in ('TradeVolume', 'TradeValue', 'Transaction'):
            result = OFFICIAL_CONNECTORS['twse_market'].parse([{**row, key: '0'}], symbol='2330', fetched_at=WHEN)
            statuses = {c.field_id: c.status for c in result.coverage}
            for field in ('market.volume', 'market.value', 'market.transactions'):
                self.assertEqual(statuses[field], 'conflicting')
                self.assertNotIn(field, {o.field_id for o in result.observations})

    def test_all_zero_totals_are_not_called_contradictory(self):
        row = {'Date': '20261016', 'Code': '2330', 'TradeVolume': '0', 'TradeValue': '0', 'Transaction': '0'}
        result = OFFICIAL_CONNECTORS['twse_market'].parse([row], symbol='2330', fetched_at=WHEN)
        self.assertTrue(all(c.status == 'available' for c in result.coverage if c.field_id in
                            {'market.volume', 'market.value', 'market.transactions'}))

    def test_research_blocks_screening_failure_and_saves_policy(self):
        from test_research import _twse_connectors
        class Provider:
            def fetch(self, symbol):
                return market(volume=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.sqlite3'
            report = research_stock('2330', provider=Provider(), connectors=_twse_connectors(),
                                    generated_at=WHEN, database_path=path)
            self.assertFalse(report.decision_eligible)
            self.assertTrue(any('low_median_turnover_proxy' in reason for reason in report.decision_blockers))
            # Even accidental caller construction cannot bypass this guard.
            self.assertFalse(replace(report, status='complete', decision_blockers=()).decision_eligible)
            with ResearchDatabase(path) as database:
                row = database.connection.execute('SELECT rationale_json FROM conclusions WHERE run_id=?', (report.run_id,)).fetchone()
                details = json.loads(row[0])
                self.assertEqual(details['analysis_snapshot']['screening_policy_version'], 'selection-safety-1')
                self.assertEqual(details['analysis_snapshot']['min_median_turnover_proxy'], 10_000_000)
                self.assertIsNone(details['analysis_snapshot']['entry_low'])
                self.assertFalse(details['full_inputs_replayable'])


if __name__ == '__main__':
    unittest.main()
