"""Read-only audit probes, deterministic fixtures; exit 1 means defects reproduced.

Run: PYTHONPATH=src python audit/reproduce_findings.py
No live requests and no production-file edits. SQLite databases are in memory.
"""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
import math
from pathlib import Path
from unittest.mock import patch

from tw_stock_swing.analysis import analyze_bars
from tw_stock_swing.catalog import load_catalog
from tw_stock_swing.cli import main
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.providers import DataProviderError, YahooFinanceProvider
from tw_stock_swing.research import _health, _coverage_items
from tw_stock_swing.sources import OFFICIAL_CONNECTORS
from tw_stock_swing.storage import ResearchDatabase

WHEN = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
results = []


def record(case, expected, actual, defect):
    results.append(dict(case=case, expected=expected, actual=actual,
                        defect_reproduced=bool(defect)))


def market(factor=1.0):
    days = []
    cursor = date(2026, 10, 16)
    while len(days) < 90:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    bars = tuple(PriceBar(day, 100, 101, 99, 100, 1000000, 100 * factor)
                 for day in reversed(days))
    return MarketData('2330.TW', 'synthetic', bars, 'audit synthetic')


# Same price series and constant adjustment factor: return must be invariant.
adjusted = analyze_bars(market(0.8), as_of=WHEN)
record('A01_adjusted_return', '0% for constant raw and adjusted prices',
       adjusted.change_20d_pct, adjusted.change_20d_pct != 0)

try:
    historical = analyze_bars(market(), as_of=WHEN - timedelta(days=14))
except ValueError as exc:
    if '截止時間' not in str(exc):
        raise
    record('A02_historical_cutoff', 'reject future data before scoring', str(exc), False)
else:
    record('A02_historical_cutoff', 'no bars later than 2026-10-02 used, or reject',
           dict(data_date=str(historical.data_date), status=historical.freshness_status,
                score=historical.score), historical.data_date > date(2026, 10, 2))


def fixture(connector_id):
    payload = json.loads((ROOT / 'tests/fixtures' / f'{connector_id}.json').read_text())
    return payload


connector = OFFICIAL_CONNECTORS['twse_market']
payload = fixture('twse_market')
payload[0]['ClosingPrice'] = 'NaN'
parsed = connector.parse(payload, symbol='2330', fetched_at=WHEN)
close = next((o for o in parsed.observations if o.field_id == 'market.close'), None)
record('A03_nonfinite_official_number', 'reject or mark invalid/missing',
       dict(value=str(close.value) if close else None, status=next(c.status for c in parsed.coverage
                                               if c.field_id == 'market.close')),
       close is not None and isinstance(close.value, float) and not math.isfinite(close.value))

payload = fixture('twse_market')
payload[0].pop('Transaction')
parsed = connector.parse(payload, symbol='2330', fetched_at=WHEN)
health = _health(parsed, WHEN + timedelta(days=14))
others = tuple(OFFICIAL_CONNECTORS[c].parse(fixture(c), symbol='2330', fetched_at=WHEN)
               for c in ('twse_institutional', 'twse_margin'))
coverage, _ = _coverage_items(load_catalog(), (parsed, *others), WHEN + timedelta(days=14))
close_status = next(c.status for c in coverage if c.field_id == 'market.close')
record('A04_stale_partial_masks_age', 'old close still marked stale despite another missing field',
       dict(health=health.status, close_status=close_status), close_status == 'available')

# String timestamps are supported by the public API; equal instants need equal queries.
with ResearchDatabase() as db:
    db.migrate()
    db.upsert_source('audit', 'audit', 'test')
    iid = db.add_instrument('2330', 'TWSE', 'audit', WHEN)
    db.add_observation(instrument_id=iid, field_id='market.close', value=100,
                       unit='TWD', valid_time='2026-10-16T00:00:00+00:00',
                       known_at='2026-10-16T10:00:00+08:00',
                       system_recorded_at='2026-10-16T02:00:00+00:00', source_id='audit')
    rows = db.observations_as_of(iid, '2026-10-16T03:00:00+00:00')
    record('A05_timezone_asof', 'one row: 10:00+08 is earlier than 03:00 UTC',
           len(rows), len(rows) != 1)

current = analyze_bars(market(), as_of=WHEN)
output = StringIO()
with patch('tw_stock_swing.cli._analyze_one',
           side_effect=[current, DataProviderError('synthetic outage')]), redirect_stdout(output):
    exit_code = main(['scan', '2330', '2317', '--workers', '1', '--json'])
scan = json.loads(output.getvalue())
record('A06_scan_partial_failure', 'partial/error status and nonzero exit when one requested stock fails',
       dict(status=scan['status'], exit_code=exit_code, errors=scan['errors']),
       scan['status'] == 'ok' and exit_code == 0 and bool(scan['errors']))
record('A07_scan_gate', 'technical-only scan does not expose formal trade levels without decision gate',
       dict(entry_low=scan['results'][0]['entry_low'],
            scope=scan.get('scope'), decision_eligible=scan.get('decision_eligible')),
       scan['results'][0]['entry_low'] is not None and 'decision_eligible' not in scan)

# The raw provider accepts identity from the response rather than validating request.
payload = {'chart': {'error': None, 'result': [{
    'meta': {'symbol': '2317.TW', 'longName': 'wrong identity'},
    'timestamp': [int(WHEN.timestamp())],
    'indicators': {'quote': [{'open': [100], 'high': [101], 'low': [99],
                              'close': [100], 'volume': [1000]}]}}]}}
try:
    data = YahooFinanceProvider()._parse(payload, '2330.TW')
except DataProviderError as exc:
    if '身分不一致' not in str(exc):
        raise
    record('A08_identity_mismatch', 'reject response symbol mismatch', str(exc), False)
else:
    record('A08_identity_mismatch', 'reject response symbol different from requested 2330.TW',
           data.symbol, data.symbol != '2330.TW')

payload = fixture('twse_market')
payload[0]['HighestPrice'] = '1'
parsed = connector.parse(payload, symbol='2330', fetched_at=WHEN)
record('A09_invalid_official_ohlc', 'reject high below low/open/close',
       {c.field_id: c.status for c in parsed.coverage if c.field_id.startswith('market.')},
       all(c.status == 'available' for c in parsed.coverage))

summary = {'baseline': 'f0ba29b003dc9b28e119611032a34321c8be641e',
           'cases': results, 'defects_reproduced': sum(r['defect_reproduced'] for r in results)}
print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
raise SystemExit(1 if summary['defects_reproduced'] else 0)
