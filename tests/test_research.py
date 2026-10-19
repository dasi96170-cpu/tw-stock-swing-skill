from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from tw_stock_swing.coverage import CoverageEntry
from tw_stock_swing.formatting import format_research
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.research import research_stock
from tw_stock_swing.sources import (
    ConnectorResult,
    TpexInstitutionalConnector,
    TpexMarginConnector,
    TpexMarketConnector,
    TwseInstitutionalConnector,
    TwseMarginConnector,
    TwseMarketConnector,
)


FIXTURES = Path(__file__).with_name("fixtures")
WHEN = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


class FixedProvider:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def fetch(self, _symbol: str) -> MarketData:
        latest = WHEN.astimezone(ZoneInfo("Asia/Taipei")).date()
        start = latest - timedelta(days=89)
        bars = tuple(
            PriceBar(
                date=start + timedelta(days=index),
                open=100 + index * 0.2,
                high=101 + index * 0.2,
                low=99 + index * 0.2,
                close=100.5 + index * 0.2,
                adjusted_close=100.5 + index * 0.2,
                volume=2_000_000 if index == 89 else 1_000_000,
            )
            for index in range(90)
        )
        return MarketData(self.symbol, "歷史資料名稱", bars, "固定 Yahoo 測試資料")


class StaticConnector:
    def __init__(self, result: ConnectorResult) -> None:
        self.connector_id = result.connector_id
        self.source_id = result.source_id
        self.endpoint = result.endpoint
        self.result = result
        self.calls: list[tuple[str, float]] = []

    def fetch_safe(self, symbol: str, *, timeout: float = 20.0) -> ConnectorResult:
        self.calls.append((symbol, timeout))
        return self.result


def _fixture_result(connector: object, filename: str, symbol: str) -> ConnectorResult:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    return connector.parse(payload, symbol=symbol, fetched_at=WHEN)  # type: ignore[attr-defined]


def _twse_connectors() -> dict[str, StaticConnector]:
    connectors = (
        (TwseMarketConnector(), "twse_market.json"),
        (TwseInstitutionalConnector(), "twse_institutional.json"),
        (TwseMarginConnector(), "twse_margin.json"),
    )
    return {
        connector.connector_id: StaticConnector(_fixture_result(connector, fixture, "2330"))
        for connector, fixture in connectors
    }


def _tpex_connectors() -> dict[str, StaticConnector]:
    connectors = (
        (TpexMarketConnector(), "tpex_market.json"),
        (TpexInstitutionalConnector(), "tpex_institutional.json"),
        (TpexMarginConnector(), "tpex_margin.json"),
    )
    return {
        connector.connector_id: StaticConnector(_fixture_result(connector, fixture, "6488"))
        for connector, fixture in connectors
    }


class ResearchTests(unittest.TestCase):
    def test_twse_research_combines_analysis_official_data_and_coverage(self) -> None:
        connectors = _twse_connectors()
        report = research_stock(
            "2330",
            provider=FixedProvider("2330.TW"),  # type: ignore[arg-type]
            connectors=connectors,
            generated_at=WHEN,
        )

        self.assertEqual(report.status, "insufficient")
        self.assertFalse(report.decision_eligible)
        self.assertEqual(report.market, "TWSE")
        self.assertEqual(report.name, "台積電")
        self.assertEqual(report.observations_by_id["identity.symbol"].value, "2330")
        self.assertEqual(
            report.observations_by_id["institutional.foreign.net"].source_id,
            "twse_openapi",
        )
        self.assertTrue(any("外資單日" in finding for finding in report.official_findings))
        self.assertTrue(any("融資餘額較前日" in finding for finding in report.official_findings))
        self.assertTrue(all(connector.calls == [("2330", 20.0)] for connector in connectors.values()))
        self.assertEqual(len({item.field_id for item in report.coverage}), len(report.coverage))
        self.assertIn("月營收", {item.name for item in report.missing_p0_categories})
        payload = report.to_dict()
        self.assertEqual(payload["scope"], "market_technical_institutional_margin")
        self.assertEqual(payload["official"]["market"]["market.close"]["source_id"], "twse_openapi")
        self.assertIsNone(payload["analysis"]["entry_low"])

    def test_market_suffix_routes_to_tpex_connectors(self) -> None:
        connectors = _tpex_connectors()
        report = research_stock(
            "6488.TWO",
            provider=FixedProvider("6488.TWO"),  # type: ignore[arg-type]
            connectors=connectors,
            generated_at=WHEN,
        )
        self.assertEqual(report.market, "TPEx")
        self.assertEqual(report.name, "環球晶")
        self.assertTrue(all(connector.calls == [("6488", 20.0)] for connector in connectors.values()))
        self.assertTrue(any("收盤價衝突" in item for item in report.market_conflicts))
        close_coverage = next(item for item in report.coverage if item.field_id == "market.close")
        self.assertEqual(close_coverage.status, "conflicting")

    def test_official_failure_returns_partial_report_instead_of_losing_analysis(self) -> None:
        connectors = _twse_connectors()
        original = connectors["twse_institutional"].result
        failure = "network blocked"
        connectors["twse_institutional"] = StaticConnector(
            ConnectorResult(
                connector_id=original.connector_id,
                source_id=original.source_id,
                endpoint=original.endpoint,
                fetched_at=WHEN,
                observations=(),
                coverage=tuple(
                    CoverageEntry(field_id, "failed", failure, original.source_id, WHEN)
                    for field_id in sorted({item.field_id for item in original.coverage})
                ),
                error=failure,
            )
        )

        report = research_stock(
            "2330",
            provider=FixedProvider("2330.TW"),  # type: ignore[arg-type]
            connectors=connectors,
            generated_at=WHEN,
        )
        self.assertEqual(report.status, "insufficient")
        self.assertGreater(report.analysis.score, 0)
        self.assertNotIn("institutional.foreign.net", report.observations_by_id)
        self.assertTrue(any("network blocked" in warning for warning in report.warnings))
        institutional_gate = next(
            item for item in report.coverage if item.field_id == "category.institutional"
        )
        self.assertEqual(institutional_gate.status, "failed")

    def test_stale_official_data_is_visible_and_marks_report_partial(self) -> None:
        report = research_stock(
            "2330",
            provider=FixedProvider("2330.TW"),  # type: ignore[arg-type]
            connectors=_twse_connectors(),
            generated_at=WHEN + timedelta(days=10),
        )
        self.assertEqual(report.status, "insufficient")
        self.assertTrue(any(source.status == "stale" for source in report.source_health))
        self.assertTrue(any("可能已過期" in warning for warning in report.warnings))

    def test_human_report_exposes_sources_and_scope_limits(self) -> None:
        report = research_stock(
            "2330",
            provider=FixedProvider("2330.TW"),  # type: ignore[arg-type]
            connectors=_twse_connectors(),
            generated_at=WHEN,
        )
        text = format_research(report)
        self.assertIn("三大法人（單日淨額）", text)
        self.assertIn("融資融券", text)
        self.assertIn("來源健康", text)
        self.assertIn("財報、營收、估值、外部新聞及其他重大事件缺口仍未完成", text)
        self.assertIn("未查到公告不代表沒有事件", text)
        self.assertIn("正式交易計畫：未產生", text)

    def test_research_is_persisted_with_evidence_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "research.sqlite3"
            report = research_stock(
                "2330",
                provider=FixedProvider("2330.TW"),  # type: ignore[arg-type]
                connectors=_twse_connectors(),
                generated_at=WHEN,
                database_path=database_path,
            )
            self.assertIsNotNone(report.run_id)
            import sqlite3
            with sqlite3.connect(database_path) as connection:
                run = connection.execute(
                    "SELECT status, completed_at FROM research_runs WHERE run_id=?",
                    (report.run_id,),
                ).fetchone()
                self.assertEqual(run[0], "insufficient")
                self.assertIsNotNone(run[1])
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM evidence_observations").fetchone()[0], 0)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM conclusion_evidence").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
