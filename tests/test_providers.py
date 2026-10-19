import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tw_stock_swing.providers import (
    DataProviderError,
    MAX_YAHOO_RESPONSE_BYTES,
    YahooFinanceProvider,
    symbol_candidates,
)


class ProviderTests(unittest.TestCase):
    def test_yahoo_http_layer_rejects_declared_oversized_response(self) -> None:
        class Response:
            headers = {"Content-Length": str(MAX_YAHOO_RESPONSE_BYTES + 1)}

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def geturl(self) -> str:
                return "https://query1.finance.yahoo.com/test"

        provider = YahooFinanceProvider(opener=lambda *_args, **_kwargs: Response())
        with self.assertRaisesRegex(DataProviderError, "大小超過限制"):
            provider._fetch_candidate("2330.TW", "6mo")

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
                        "meta": {"symbol": "2330.TW", "longName": "台積電", "currency": "TWD", "instrumentType": "EQUITY"},
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
                            ],
                            "adjclose": [{"adjclose": [98.5, None]}],
                        },
                    }
                ],
            }
        }
        data = YahooFinanceProvider()._parse(payload, "2330.TW")
        self.assertEqual(data.name, "台積電")
        self.assertEqual(len(data.bars), 1)
        self.assertEqual(data.bars[0].date.isoformat(), "2026-01-02")
        self.assertEqual(data.bars[0].adjusted_close, 98.5)


if __name__ == "__main__":
    unittest.main()
