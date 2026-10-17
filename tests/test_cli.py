import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from tw_stock_swing.cli import main
from tw_stock_swing.coverage import CoverageEntry
from tw_stock_swing.models import MarketData, PriceBar
from tw_stock_swing.sources import ConnectorResult


def fixture_market_data() -> MarketData:
    start = date.today() - timedelta(days=80)
    bars = tuple(
        PriceBar(
            date=start + timedelta(days=index),
            open=100.0 + index * 0.2,
            high=101.0 + index * 0.2,
            low=99.5 + index * 0.2,
            close=100.5 + index * 0.2,
            volume=1_000_000 + index * 1_000,
        )
        for index in range(80)
    )
    return MarketData("2330.TW", "台積電", bars, "固定測試資料")


class CliTests(unittest.TestCase):
    @patch("tw_stock_swing.cli.YahooFinanceProvider.fetch", return_value=fixture_market_data())
    def test_analyze_json_contract(self, _fetch: object) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["analyze", "2330", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["analysis"]["symbol"], "2330.TW")
        self.assertIn(payload["analysis"]["verdict"], {"候選", "觀察", "避開"})

    def test_invalid_symbol_returns_structured_error(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["analyze", "TSMC", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "error")

    def test_catalog_command_is_machine_readable(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["catalog"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(payload["field_count"], 23)
        self.assertIn("category.identity", {field["id"] for field in payload["fields"]})

    @patch("tw_stock_swing.cli.research_stock")
    def test_research_json_contract(self, research: object) -> None:
        research.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            status="partial",
            to_dict=lambda: {"symbol": "2330.TW", "market": "TWSE", "warnings": ["fixture"]},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["research", "2330", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["research"]["symbol"], "2330.TW")

    def test_init_db_command_creates_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["init-db", str(path)])
            self.assertEqual(exit_code, 0)
            self.assertTrue(path.exists())

    def test_sources_failure_exit_code_takes_priority_over_partial(self) -> None:
        when = datetime(2026, 10, 17, tzinfo=timezone.utc)

        class Connector:
            def __init__(self, result: ConnectorResult) -> None:
                self.result = result
                self.source_id = result.source_id

            def fetch_safe(self, *_args: object, **_kwargs: object) -> ConnectorResult:
                return self.result

        partial = ConnectorResult(
            "partial", "twse_openapi", "https://openapi.twse.com.tw/test", when,
            (), (CoverageEntry("market.close", "available", "fixture", "twse_openapi", when),),
            valid_time_verified=False,
        )
        failed = ConnectorResult(
            "failed", "tpex_openapi", "https://www.tpex.org.tw/test", when,
            (), (CoverageEntry("market.close", "failed", "blocked", "tpex_openapi", when),),
            error="blocked",
        )
        output = io.StringIO()
        with patch(
            "tw_stock_swing.cli.OFFICIAL_CONNECTORS",
            {"partial": Connector(partial), "failed": Connector(failed)},
        ), redirect_stdout(output):
            exit_code = main(["sources", "--live"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["live"]["partial"]["status"], "partial")
        self.assertEqual(payload["live"]["failed"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
