"""Synthetic records with official schema names; no live tests in CI."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from tw_stock_swing.catalog import load_catalog
from tw_stock_swing.cli import main
from tw_stock_swing.sources.company import COMPANY_CONNECTORS, _date, _published, fetch_company_sources
from tw_stock_swing.sources.official import ConnectorError
from tw_stock_swing.research import research_stock
from tw_stock_swing.storage import ResearchDatabase
from test_research import FixedProvider, _twse_connectors, WHEN

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def profile(market='TWSE'):
    if market == 'TWSE':
        return {'出表日期': '1151016', '公司代號': '2330', '公司名稱': '測試股份有限公司',
                '英文簡稱': 'TEST', '產業別': '24', '上市日期': '19940905',
                '實收資本額': '10000000', '已發行普通股數或TDR原股發行股數': '1000000'}
    return {'Date': '1151016', 'SecuritiesCompanyCode': '6488', 'CompanyName': '測試公司',
            'Symbol': 'TEST', 'SecuritiesIndustryCode': '24', 'DateOfListing': '20111018',
            'Paidin.Capital.NTDollars': '10000000', 'IssueShares': '1000000'}


def announcement(market='TWSE'):
    return {('出表日期' if market == 'TWSE' else 'Date'): '1151016',
            ('公司代號' if market == 'TWSE' else 'SecuritiesCompanyCode'): '2330' if market == 'TWSE' else '6488',
            '主旨 ': '公告測試事項', '發言日期': '1151016', '發言時間': '70003',
            '符合條款': '20', '事實發生日': '115/10/15', '說明': '不可保存的測試全文 SECRET_BODY'}


def parse(kind, rows, market='TWSE', now=NOW):
    name = ('twse' if market == 'TWSE' else 'tpex') + '_' + kind
    return COMPANY_CONNECTORS[name].parse(rows, symbol='2330' if market == 'TWSE' else '6488', fetched_at=now)


class CompanyTests(unittest.TestCase):
    def test_contracts_are_registered(self):
        fields = load_catalog().by_id
        for connector in COMPANY_CONNECTORS.values():
            self.assertLessEqual(connector.contract_fields, fields.keys())

    def test_both_official_profile_formats(self):
        for market in ('TWSE', 'TPEx'):
            result = parse('profile', [profile(market)], market)
            self.assertEqual(result.fields['identity.industry_code'], '24')
            self.assertEqual(result.fields['identity.paid_in_capital'], 10000000)
            self.assertTrue(all(c.status == 'available' for c in result.coverage))

    def test_profile_missing_invalid_fields_have_explicit_status(self):
        row = profile()
        row.pop('英文簡稱')
        row['實收資本額'] = 'NaN'
        row['上市日期'] = '20990101'
        result = parse('profile', [row])
        self.assertEqual(len(result.coverage), 6)
        self.assertEqual(sum(c.status == 'missing' for c in result.coverage), 3)

    def test_roc_gregorian_dates_and_short_times(self):
        self.assertEqual(_date('115/1/2'), _date('20260102'))
        self.assertEqual(str(_date('19000101')), '1900-01-01')
        self.assertEqual(_published('1151016', '70003').hour, 7)
        self.assertEqual(_published('1151016', '5').second, 5)
        self.assertIsNone(_published('1151016', '250000'))
        self.assertIsNone(_date('1150230'))

    def test_empty_and_nonmatching_snapshots_do_not_prove_absence(self):
        empty = parse('announcements', [])
        self.assertFalse(empty.to_dict()['absence_proven'])
        self.assertIsNone(empty.source_date)
        self.assertTrue(any(c.status == 'unverified' for c in empty.coverage))
        row = announcement()
        row['公司代號'] = '2317'
        result = parse('announcements', [row])
        self.assertEqual(result.announcements, ())
        self.assertFalse(result.to_dict()['absence_proven'])
        self.assertEqual(next(c.status for c in result.coverage if c.field_id.endswith('.announcements')), 'available')
        self.assertIn('no_matching_rows_in_returned_snapshot_not_proof_of_absence', result.issues)

    def test_missing_snapshot_date_is_never_replaced_by_fetch_date(self):
        row = profile()
        row.pop('出表日期')
        result = parse('profile', [row])
        self.assertIsNone(result.source_date)
        self.assertTrue(all(c.status == 'unverified' for c in result.coverage))

    def test_stale_future_and_mixed_snapshot_dates(self):
        for stamp, expected in (('1151001', 'stale'), ('1151018', 'unverified')):
            row = profile()
            row['出表日期'] = stamp
            self.assertTrue(all(c.status == expected for c in parse('profile', [row]).coverage))
        row = announcement()
        other = {**row, '公司代號': '2317', '出表日期': '1151015'}
        self.assertIsNone(parse('announcements', [row, other]).source_date)

    def test_both_announcement_formats_exclude_body_and_preserve_dates(self):
        for market in ('TWSE', 'TPEx'):
            result = parse('announcements', [announcement(market)], market)
            event = result.announcements[0]
            self.assertEqual(event.event_date, '2026-10-15')
            self.assertEqual(event.published_at, '2026-10-16T07:00:03+08:00')
            self.assertEqual(event.headline, '公告測試事項')
            self.assertNotIn('SECRET_BODY', json.dumps(result.to_dict()))
            self.assertTrue(all(c.status == 'available' for c in result.coverage))

    def test_duplicate_removed_but_correction_retained(self):
        row = announcement()
        result = parse('announcements', [row, row, {**row, '說明': '更正後全文'}])
        self.assertEqual(len(result.announcements), 2)
        self.assertEqual(len({e.event_key for e in result.announcements}), 1)
        self.assertEqual(len({e.revision_hash for e in result.announcements}), 2)

    def test_future_or_unknown_publication_is_unverified(self):
        for changes in ({'發言日期': '1151017'}, {'發言時間': ''}, {'發言日期': 'bad'}):
            result = parse('announcements', [{**announcement(), **changes}])
            self.assertTrue(result.announcements[0].issues)
            self.assertEqual(next(c.status for c in result.coverage if c.field_id.endswith('.published_at')), 'unverified')

    def test_publication_after_research_cutoff_is_not_treated_as_known(self):
        connector = COMPANY_CONNECTORS['twse_announcements']
        cutoff = datetime(2026, 10, 15, 23, tzinfo=timezone.utc)  # 07:00 Taipei
        result = connector.parse([announcement()], symbol='2330', fetched_at=NOW, as_of=cutoff)
        self.assertIn('future_publication', result.announcements[0].issues)
        self.assertEqual(next(c.status for c in result.coverage if c.field_id.endswith('.published_at')), 'unverified')
        self.assertEqual(result.evaluated_at, cutoff)

    def test_schema_upgrade_is_additive_and_repeatable(self):
        with ResearchDatabase() as database:
            database.migrate()
            database.upsert_source('twse_openapi', 'TWSE', 'official')
            run_id = database.start_run('2330', NOW, 'TWSE')
            # Simulate the exact older schema: only the new table is absent.
            database.connection.execute('DROP TABLE research_supplements')
            database.connection.execute('PRAGMA user_version=2')
            database.migrate()
            database.migrate()
            self.assertEqual(database.connection.execute('SELECT run_id FROM research_runs').fetchone()[0], run_id)
            self.assertEqual(database.connection.execute('SELECT count(*) FROM research_supplements').fetchone()[0], 0)
            self.assertEqual(database.connection.execute('PRAGMA user_version').fetchone()[0], 3)

    def test_shape_errors_and_unidentifiable_symbols_fail_loudly(self):
        for rows in ({'data': []}, [None], [{'主旨': 'test'}], [profile(), profile()]):
            with self.subTest(rows=rows), self.assertRaises(ConnectorError):
                parse('profile', rows)

    def test_fetch_failure_retains_every_contract_field(self):
        def broken(*args):
            raise ConnectorError('blocked')
        for connector in COMPANY_CONNECTORS.values():
            result = connector.fetch_safe('2330', fetcher=broken)
            self.assertEqual({c.field_id for c in result.coverage}, connector.contract_fields)
            self.assertTrue(all(c.status == 'failed' for c in result.coverage))

    def test_raw_hash_and_registry_failure_are_explicit(self):
        connector = COMPANY_CONNECTORS['twse_profile']
        result = connector.fetch_safe('2330', fetched_at=NOW, fetcher=lambda *args: json.dumps([profile()]).encode())
        self.assertEqual(len(result.content_hash), 64)
        self.assertTrue(all(r.error == 'connector_not_configured' for r in fetch_company_sources('2330', 'TWSE', connectors={})))

    def test_company_cli_is_metadata_only(self):
        output = StringIO()
        results = (parse('profile', [profile()]), parse('announcements', [announcement()]))
        with patch('tw_stock_swing.cli.fetch_company_sources', return_value=results), redirect_stdout(output):
            code = main(['company', '2330', '--market', 'TWSE'])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload['decision_eligible'])
        self.assertNotIn('SECRET_BODY', output.getvalue())

    def test_every_contract_field_has_one_status_even_when_data_is_absent(self):
        for connector in COMPANY_CONNECTORS.values():
            row = profile(connector.market) if connector.kind == 'profile' else announcement(connector.market)
            symbol = '2330' if connector.market == 'TWSE' else '6488'
            for payload in ([], [row]):
                result = connector.parse(payload, symbol=symbol, fetched_at=NOW)
                field_ids = [item.field_id for item in result.coverage]
                self.assertEqual(set(field_ids), connector.contract_fields)
                self.assertEqual(len(field_ids), len(set(field_ids)))

    def test_company_cli_partial_and_failure_do_not_report_success(self):
        good = parse('profile', [profile()])
        absent = parse('announcements', [])
        failed = COMPANY_CONNECTORS['twse_announcements'].failed('2330', NOW, 'blocked')
        for result, expected_code, expected_status in ((absent, 3, 'partial'), (failed, 2, 'error')):
            output = StringIO()
            with patch('tw_stock_swing.cli.fetch_company_sources', return_value=(good, result)), redirect_stdout(output):
                code = main(['company', '2330', '--market', 'TWSE'])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, expected_code)
            self.assertEqual(payload['status'], expected_status)
            self.assertFalse(payload['decision_eligible'])

    def test_research_integrates_and_persists_metadata_with_evidence(self):
        class Static:
            def __init__(self, result):
                self.result = result
            def fetch_safe(self, *args, **kwargs):
                return self.result
        sources = {name: Static(replace(parse(kind, [row]), content_hash='1' * 64)) for name, kind, row in
                   [('twse_profile', 'profile', profile()), ('twse_announcements', 'announcements', announcement())]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research.sqlite3'
            report = research_stock('2330', provider=FixedProvider('2330.TW'), connectors=_twse_connectors(),
                                    company_connectors=sources, generated_at=WHEN, database_path=path)
            self.assertFalse(report.decision_eligible)
            self.assertEqual(len(report.company_sources), 2)
            by_id = {c.field_id: c for c in report.coverage}
            self.assertEqual(by_id['identity.legal_name'].status, 'available')
            with ResearchDatabase(path) as database:
                database.migrate()
                rows = database.connection.execute('SELECT payload_json FROM research_supplements WHERE run_id=?', (report.run_id,)).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertNotIn('SECRET_BODY', str([tuple(r) for r in rows]))
                evidence = database.connection.execute("SELECT published_at FROM evidence WHERE evidence_status='metadata_only_unassessed'").fetchone()
                self.assertIsNotNone(evidence[0])
                profile_count = database.connection.execute("SELECT count(*) FROM evidence WHERE evidence_status='profile_metadata_unassessed'").fetchone()[0]
                self.assertEqual(profile_count, 6)
                self.assertEqual(database.connection.execute('PRAGMA user_version').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
