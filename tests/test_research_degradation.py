"""S07: failures stay visible without losing independent official evidence."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tw_stock_swing.cli import main
from tw_stock_swing.formatting import format_research
from tw_stock_swing.providers import DataProviderError
from tw_stock_swing.research import research_stock
from tw_stock_swing.sources.company import COMPANY_CONNECTORS, fetch_company_sources
from tw_stock_swing.storage import ResearchDatabase
from test_research import FixedProvider, WHEN, _twse_connectors, _tpex_connectors
from test_company_sources import profile, announcement


class BrokenYahoo:
    def fetch(self, symbol):
        raise DataProviderError('synthetic Yahoo outage')


class StaticCompany:
    def __init__(self, name, *, present=True, stale=False):
        self.name, self.present, self.stale = name, present, stale
        self.calls = []

    def fetch_safe(self, symbol, *, timeout, as_of):
        self.calls.append((symbol, timeout))
        connector = COMPANY_CONNECTORS[self.name]
        row = profile(connector.market) if connector.kind == 'profile' else announcement(connector.market)
        code = '公司代號' if connector.market == 'TWSE' else 'SecuritiesCompanyCode'
        row[code] = symbol if self.present else '9999'
        if self.stale:
            row['出表日期' if connector.market == 'TWSE' else 'Date'] = '1151001'
        return connector.parse([row], symbol=symbol, fetched_at=WHEN, as_of=as_of)


class BrokenConnector:
    def fetch_safe(self, *args, **kwargs):
        raise OSError('synthetic source outage')


def companies(market='TWSE'):
    return {name: StaticCompany(name, present=connector.market == market)
            for name, connector in COMPANY_CONNECTORS.items()}


class DegradedResearchTests(unittest.TestCase):
    def research(self, symbol='2330.TW', **kwargs):
        options = dict(provider=BrokenYahoo(), connectors=_twse_connectors(),
                       company_connectors=companies(), generated_at=WHEN)
        options.update(kwargs)
        return research_stock(symbol, **options)

    def assert_blocked(self, report):
        self.assertEqual(report.status, 'insufficient')
        self.assertIsNone(report.analysis)
        self.assertFalse(report.decision_eligible)
        data = report.to_dict()
        self.assertIsNone(data['analysis'])
        self.assertTrue(data['analysis_error'])
        self.assertEqual(next(c.status for c in report.coverage if c.field_id == 'category.technical'), 'failed')
        self.assertEqual(len(report.coverage), len({c.field_id for c in report.coverage}))
        self.assertFalse(replace(report, status='complete', decision_blockers=()).decision_eligible)

    def test_explicit_twse_survives_yahoo_failure(self):
        connectors, company = _twse_connectors(), companies()
        report = self.research(connectors=connectors, company_connectors=company)
        self.assert_blocked(report)
        self.assertEqual(report.market_resolution, 'requested_market')
        self.assertIn('institutional.foreign.net', report.observations_by_id)
        self.assertTrue(all(c.calls for c in connectors.values()))
        self.assertTrue(company['twse_announcements'].calls)
        self.assertFalse(company['tpex_profile'].calls)
        self.assertTrue(report.company_sources[1].announcements)

    def test_explicit_tpex_does_not_fall_back_to_twse(self):
        connectors = _tpex_connectors()
        report = self.research('6488.TWO', connectors=connectors, company_connectors=companies('TPEx'))
        self.assert_blocked(report)
        self.assertEqual((report.market, report.symbol), ('TPEx', '6488.TWO'))
        self.assertTrue(all(c.calls == [('6488', 20)] for c in connectors.values()))

    def test_bare_code_uses_unique_fresh_official_profiles(self):
        report = self.research('2330')
        self.assert_blocked(report)
        self.assertEqual((report.market, report.symbol, report.market_resolution), ('TWSE', '2330.TW', 'official_profiles'))
        self.assertEqual(len(report.company_sources), 4)  # Keep routing provenance too.
        self.assertIsNone(report.routing_error)
        self.assertEqual(next(c.status for c in report.coverage if c.field_id == 'identity.legal_name'), 'available')

    def test_bare_code_resolves_tpex(self):
        report = self.research('6488', connectors=_tpex_connectors(), company_connectors=companies('TPEx'))
        self.assertEqual((report.market, report.symbol), ('TPEx', '6488.TWO'))
        self.assert_blocked(report)

    def test_ambiguous_profiles_never_mix_observations(self):
        registry = {name: StaticCompany(name) for name in COMPANY_CONNECTORS}
        connectors = _twse_connectors()
        report = self.research('2330', company_connectors=registry, connectors=connectors)
        self.assert_blocked(report)
        self.assertEqual((report.market, report.symbol), ('UNRESOLVED', '2330'))
        self.assertTrue(report.routing_error)
        self.assertEqual(report.official_observations, ())
        self.assertTrue(all(not c.calls for c in connectors.values()))
        self.assertTrue(all(h.status == 'not_requested' for h in report.source_health[1:]))
        self.assertEqual(len(report.company_sources), 4)

    def test_competing_profile_failure_does_not_establish_exclusivity(self):
        registry = companies()
        registry['tpex_profile'] = BrokenConnector()
        report = self.research('2330', company_connectors=registry)
        self.assertEqual(report.market, 'UNRESOLVED')
        self.assert_blocked(report)
        self.assertTrue(any(r.error for r in report.company_sources))

    def test_stale_profiles_do_not_resolve_market(self):
        registry = companies()
        registry['twse_profile'] = StaticCompany('twse_profile', stale=True)
        report = self.research('2330', company_connectors=registry)
        self.assertEqual(report.market, 'UNRESOLVED')

    def test_no_matching_company_is_not_assumed_twse(self):
        registry = {name: StaticCompany(name, present=False) for name in COMPANY_CONNECTORS}
        report = self.research('2330', company_connectors=registry)
        self.assertEqual(report.market, 'UNRESOLVED')
        self.assertEqual(report.official_observations, ())

    def test_invalid_symbol_does_not_trigger_any_fetch(self):
        provider = BrokenYahoo()
        with patch.object(provider, 'fetch') as fetch, patch('tw_stock_swing.research.fetch_company_sources') as company:
            with self.assertRaises(ValueError):
                self.research('TSMC', provider=provider)
            fetch.assert_not_called()
            company.assert_not_called()

    def test_wrong_response_identity_is_not_used_for_routing(self):
        report = self.research('2330.TW', provider=FixedProvider('6488.TWO'))
        self.assert_blocked(report)
        self.assertEqual(report.market, 'TWSE')
        self.assertIn('身分不一致', report.analysis_error)

    def test_invalid_technical_data_preserves_official_evidence(self):
        class InvalidData:
            def fetch(self, symbol):
                good = FixedProvider('2330.TW').fetch(symbol)
                return replace(good, bars=tuple(replace(b, adjusted_close=None) for b in good.bars))
        report = self.research('2330', provider=InvalidData())
        self.assert_blocked(report)
        self.assertEqual(report.market_resolution, 'provider_symbol')
        self.assertIn('還原價格全部缺漏', report.analysis_error)
        self.assertTrue(report.official_observations)

    def test_missing_official_connector_does_not_abort_other_modules(self):
        connectors = _twse_connectors()
        del connectors['twse_market']
        report = self.research(connectors=connectors)
        self.assert_blocked(report)
        self.assertIn('institutional.foreign.net', report.observations_by_id)
        self.assertTrue(any(h.error == 'connector_not_configured' for h in report.source_health))

    def test_official_exception_is_isolated(self):
        connectors = _twse_connectors()
        connectors['twse_institutional'] = BrokenConnector()
        report = self.research(connectors=connectors)
        self.assertTrue(report.observations_by_id['market.close'])
        self.assertNotIn('institutional.foreign.net', report.observations_by_id)
        self.assertTrue(any(h.error == 'synthetic source outage' for h in report.source_health))

    def test_company_exception_does_not_discard_sibling(self):
        registry = companies()
        registry['twse_announcements'] = BrokenConnector()
        results = fetch_company_sources('2330', 'TWSE', connectors=registry, as_of=WHEN)
        self.assertTrue(results[0].fields)
        self.assertEqual(results[1].error, 'synthetic source outage')
        self.assertTrue(all(c.status == 'failed' for c in results[1].coverage))

    def test_unknown_programming_errors_are_not_silently_relabelled(self):
        class Bug:
            def fetch(self, symbol):
                raise RuntimeError('programming error')
        with self.assertRaisesRegex(RuntimeError, 'programming error'):
            self.research(provider=Bug())

    def test_degraded_report_persists_without_fabricated_yahoo_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research.sqlite3'
            report = self.research(database_path=path)
            with ResearchDatabase(path) as db:
                self.assertEqual(db.connection.execute("SELECT count(*) FROM observations WHERE source_id='yahoo_finance'").fetchone()[0], 0)
                self.assertGreater(db.connection.execute('SELECT count(*) FROM observations').fetchone()[0], 0)
                row = db.connection.execute('SELECT rationale_json FROM conclusions WHERE run_id=?', (report.run_id,)).fetchone()
                saved = json.loads(row[0])
                self.assertIsNone(saved['analysis_snapshot'])
                self.assertIn('Yahoo outage', saved['analysis_error'])
                self.assertEqual(saved['source_health_snapshot'][0]['status'], 'failed')
                self.assertEqual(db.connection.execute('SELECT count(*) FROM research_supplements').fetchone()[0], 2)

    def test_unknown_market_run_saved_without_inventing_instrument(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research.sqlite3'
            report = self.research('2330', company_connectors={}, database_path=path)
            self.assertEqual(report.market, 'UNRESOLVED')
            with ResearchDatabase(path) as db:
                self.assertEqual(db.connection.execute('SELECT count(*) FROM instruments').fetchone()[0], 0)
                self.assertEqual(db.connection.execute('SELECT count(*) FROM observations').fetchone()[0], 0)
                self.assertEqual(db.connection.execute('SELECT status FROM research_runs').fetchone()[0], 'insufficient')
                self.assertEqual(db.connection.execute('SELECT count(*) FROM research_supplements').fetchone()[0], 4)
                saved = json.loads(db.connection.execute('SELECT rationale_json FROM conclusions').fetchone()[0])
                self.assertTrue(saved['routing_error'])

    def test_text_and_cli_json_support_missing_analysis(self):
        report = self.research()
        text = format_research(report)
        self.assertIn('技術篩選：不可用', text)
        self.assertIn('外資', text)
        self.assertNotIn('參考入場', text)
        self.assertNotIn('0/100', text)
        for args in (['research', '2330.TW', '--json'], ['research', '2330.TW']):
            output = StringIO()
            with patch('tw_stock_swing.cli.research_stock', return_value=report), redirect_stdout(output):
                self.assertEqual(main(args), 4)
            if '--json' in args:
                self.assertIsNone(json.loads(output.getvalue())['research']['analysis'])

    def test_all_sources_fail_still_returns_complete_missingness(self):
        report = self.research(connectors={}, company_connectors={})
        self.assert_blocked(report)
        self.assertEqual(report.official_observations, ())
        self.assertEqual(len(report.company_sources), 2)
        self.assertEqual(len(report.coverage), 64)
        self.assertTrue(all(h.status == 'failed' for h in report.source_health))

    def test_persisted_ambiguous_routing_does_not_promote_candidate_evidence(self):
        registry = {name: StaticCompany(name) for name in COMPANY_CONNECTORS}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ambiguous.sqlite3'
            report = self.research('2330', company_connectors=registry, database_path=path)
            self.assertEqual(report.market, 'UNRESOLVED')
            with ResearchDatabase(path) as db:
                self.assertEqual(db.connection.execute('SELECT count(*) FROM research_supplements').fetchone()[0], 4)
                self.assertEqual(db.connection.execute('SELECT count(*) FROM evidence').fetchone()[0], 0)
                self.assertEqual(db.connection.execute('SELECT count(*) FROM instruments').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
