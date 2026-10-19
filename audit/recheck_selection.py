"""Offline selection audit; synthetic counterexamples, no production mutations.

Run with PYTHONPATH=src python audit/recheck_selection.py.
Exit 1 indicates at least one demonstrated remaining selection weakness.
"""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from unittest.mock import patch

from tw_stock_swing.analysis import analyze_bars
from tw_stock_swing.cli import main
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.providers import YahooFinanceProvider, DataProviderError
from tw_stock_swing.research import research_stock
from tw_stock_swing.sources import OFFICIAL_CONNECTORS

WHEN = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def dates(count):
    result, day = [], date(2026, 10, 16)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day -= timedelta(days=1)
    return list(reversed(result))


def market(symbol='2330.TW', volume=1_000_000):
    bars = tuple(PriceBar(day, 100 + i, 100.1 + i, 99.9 + i, 100 + i, volume,
                          100 + i) for i, day in enumerate(dates(100)))
    return MarketData(symbol, '合成測試，不是真實股價', bars, 'synthetic')


def yahoo_payload(data, *, currency='TWD', instrument_type='EQUITY', include_adjusted=True):
    indicators = {'quote': [{key: [getattr(b, key) for b in data.bars]
                             for key in ('open', 'high', 'low', 'close', 'volume')}]}
    if include_adjusted:
        indicators['adjclose'] = [{'adjclose': [b.adjusted_close for b in data.bars]}]
    return {'chart': {'result': [{'meta': {'symbol': data.symbol, 'currency': currency,
                                          'instrumentType': instrument_type},
        'timestamp': [int(datetime.combine(b.date, datetime.min.time(), timezone.utc).timestamp())
                      for b in data.bars], 'indicators': indicators}]}}


findings = []


def record(case, actual, reproduced):
    findings.append({'case': case, 'actual': actual, 'weakness_reproduced': bool(reproduced)})


liquid = analyze_bars(market(), as_of=WHEN)
thin = analyze_bars(market(volume=1), as_of=WHEN)
zero = analyze_bars(market(volume=0), as_of=WHEN)
record('S01_no_liquidity_or_zero_trade_gate',
       {'liquid_score': liquid.score, 'one_share_score': thin.score,
        'zero_volume_score': zero.score, 'zero_volume_verdict': zero.verdict,
        'zero_volume_freshness': zero.freshness_status},
       (thin.screening_eligible and thin.verdict == '候選') or (zero.screening_eligible and zero.verdict == '候選'))

base = market()
last = base.bars[-1]
down_close = base.bars[-2].close * 0.99
down_bar = replace(last, open=base.bars[-2].close, high=base.bars[-2].close + .1,
                   low=down_close - .1, close=down_close, adjusted_close=down_close)
ordinary = analyze_bars(replace(base, bars=(*base.bars[:-1], down_bar)), as_of=WHEN)
heavy = analyze_bars(replace(base, bars=(*base.bars[:-1], replace(down_bar, volume=10_000_000))), as_of=WHEN)
record('S02_down_day_volume_unconditionally_rewarded',
       {'close_change_pct': -1, 'ordinary_score': ordinary.score, 'heavy_score': heavy.score,
        'heavy_verdict': heavy.verdict, 'reasons': heavy.reasons}, heavy.score > ordinary.score)

# Entire dates absent: unlike null values, no parser row exists to warn about.
all_days = dates(200)
full = replace(base, bars=tuple(PriceBar(d, 100+i, 100.1+i, 99.9+i, 100+i, 1_000_000, 100+i)
                               for i, d in enumerate(all_days)))
sparse = YahooFinanceProvider()._parse(yahoo_payload(replace(full, bars=full.bars[1::2])), full.symbol)
complete_result, sparse_result = (analyze_bars(m, as_of=WHEN) for m in (full, sparse))
record('S03_missing_whole_sessions_not_detected',
       {'full_20_observation_return': complete_result.change_20d_pct,
        'sparse_claimed_20_day_return': sparse_result.change_20d_pct,
        'sparse_window_weekday_sessions': 40, 'freshness': sparse_result.freshness_status,
        'parser_quality_issues': sparse.quality_issues},
       sparse_result.screening_eligible and sparse_result.freshness_status == 'current'
       and sparse_result.change_20d_pct != complete_result.change_20d_pct)

# 2-for-1 split with no economic return, adjusted series entirely omitted.
split_bars = tuple(PriceBar(d, 100 if i < 99 else 50, 100 if i < 99 else 50,
                            100 if i < 99 else 50, 100 if i < 99 else 50, 1000, 50)
                   for i, d in enumerate(dates(100)))
split = replace(base, bars=split_bars)
adjusted = analyze_bars(split, as_of=WHEN)
missing_adjusted = YahooFinanceProvider()._parse(yahoo_payload(split, include_adjusted=False), split.symbol)
try:
    raw = analyze_bars(missing_adjusted, as_of=WHEN)
except ValueError as exc:
    if '還原價格全部缺漏' not in str(exc):
        raise
    record('S04_all_adjusted_prices_missing_silently_falls_back', {'rejected': str(exc)}, False)
else:
    record('S04_all_adjusted_prices_missing_silently_falls_back',
           {'adjusted_return_pct': adjusted.change_20d_pct, 'raw_return_pct': raw.change_20d_pct},
           raw.change_20d_pct == -50 and raw.freshness_status == 'current')

# A split changes share-count volume with unchanged traded notional.
split_volume = replace(split, bars=tuple(replace(b, volume=1000 if i < 99 else 2000)
                                        for i, b in enumerate(split_bars)))
split_volume_result = analyze_bars(split_volume, as_of=WHEN)
record('S05_split_share_volume_creates_false_spike',
       {'notional_before': 100*1000, 'notional_after': 50*2000,
        'volume_ratio': split_volume_result.volume_ratio, 'reasons': split_volume_result.reasons},
       split_volume_result.volume_signal_usable and split_volume_result.score > adjusted.score)

output = StringIO()
with patch('tw_stock_swing.cli.YahooFinanceProvider.fetch', return_value=base), \
     patch('tw_stock_swing.cli.analyze_bars', return_value=liquid), redirect_stdout(output):
    code = main(['scan', '2330', '2330.TW', '--limit', '2', '--json'])
scanned = json.loads(output.getvalue())
record('S06_same_security_alias_occupies_multiple_ranks',
       {'exit': code, 'symbols': [r['symbol'] for r in scanned['results']],
        'eligible': scanned['eligible']}, len(scanned['results']) == 2)

# Research aborts before collecting independent official evidence on Yahoo failure.
class BrokenYahoo:
    def fetch(self, symbol):
        raise DataProviderError('synthetic Yahoo outage')

with patch('tw_stock_swing.research.fetch_company_sources', return_value=()) as company:
    try:
        report = research_stock('2330.TW', provider=BrokenYahoo(), connectors={}, generated_at=WHEN)
    except DataProviderError:
        record('S07_yahoo_failure_prevents_official_research',
               {'company_fetch_calls': company.call_count, 'report_returned': False}, company.call_count == 0)
    else:
        record('S07_yahoo_failure_prevents_official_research',
               {'company_fetch_calls': company.call_count, 'report_returned': True,
                'status': report.status, 'analysis_is_none': report.analysis is None},
               company.call_count == 0 or report.analysis is not None or report.decision_eligible)

# A correctly identified symbol alone does not validate quote currency/type.
payload = yahoo_payload(base, currency='USD', instrument_type='ETF')
try:
    parsed = YahooFinanceProvider()._parse(payload, '2330.TW')
except DataProviderError as exc:
    if '幣別／證券類型' not in str(exc):
        raise
    record('S08_currency_and_instrument_type_not_checked', {'rejected': str(exc)}, False)
else:
    record('S08_currency_and_instrument_type_not_checked', {'accepted': True}, not parsed.quality_issues)

# Daily totals: zero value and zero trades alongside positive share volume.
connector = OFFICIAL_CONNECTORS['twse_market']
row = {'Date': '20261016', 'Code': '2330', 'Name': '合成', 'OpeningPrice': '100',
       'HighestPrice': '100', 'LowestPrice': '100', 'ClosingPrice': '100', 'Change': '0',
       'TradeVolume': '1000000', 'TradeValue': '0', 'Transaction': '0'}
result = connector.parse([row], symbol='2330', fetched_at=WHEN)
record('S09_volume_value_trades_contradiction_accepted',
       {'coverage': {c.field_id: c.status for c in result.coverage}},
       all(c.status == 'available' for c in result.coverage))

print(json.dumps({'scope': 'synthetic_offline_not_live_stock_claims', 'findings': findings,
                  'weaknesses_reproduced': sum(f['weakness_reproduced'] for f in findings)},
                 ensure_ascii=False, indent=2))
raise SystemExit(1 if any(f['weakness_reproduced'] for f in findings) else 0)
