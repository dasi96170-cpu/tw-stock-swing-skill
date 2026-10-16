import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tw_stock_swing.providers import YahooFinanceProvider, symbol_candidates


class ProviderTests(unittest.TestCase):
    def test_symbol_candidates_supports_listed_and_otc(self) -> None:
        self.assertEqual(symbol_candidates("2330"), ("2330.TW", "2330.TWO"))
        self.assertEqual(symbol_candidates("6488.two"), ("6488.TWO",))

    def test_invalid_symbol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            symbol_candidates("TSMC")

    def test_parser_drops_incomplete_rows(self) -> None:
        timezone = ZoneInfo("Asia/Taipei")
        timestamps = [
            int(datetime(2026, 1, 2, tzinfo=timezone).timestamp()),
            int(datetime(2026, 1, 3, tzinfo=timezone).timestamp()),
        ]
        payload = {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {"symbol": "2330.TW", "longName": "台積電"},
                        "timestamp": timestamps,
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0, None],
                                    "high": [102.0, None],
                                    "low": [99.0, None],
                                    "close": [101.0, None],
                                    "volume": [1000, None],
                                }
                            ]
                        },
                    }
                ],
            }
        }
        data = YahooFinanceProvider()._parse(payload, "2330.TW")
        self.assertEqual(data.name, "台積電")
        self.assertEqual(len(data.bars), 1)
        self.assertEqual(data.bars[0].date.isoformat(), "2026-01-02")


if __name__ == "__main__":
    unittest.main()
