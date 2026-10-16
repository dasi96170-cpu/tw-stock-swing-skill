import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from unittest.mock import patch

from tw_stock_swing.cli import main
from tw_stock_swing.models import MarketData, PriceBar


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

    def test_init_db_command_creates_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["init-db", str(path)])
            self.assertEqual(exit_code, 0)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
