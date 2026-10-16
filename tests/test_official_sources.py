from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import unittest

from tw_stock_swing.sources import (
    ConnectorError,
    TpexInstitutionalConnector,
    TpexMarginConnector,
    TpexMarketConnector,
    TwseInstitutionalConnector,
    TwseMarginConnector,
    TwseMarketConnector,
)


FIXTURES = Path(__file__).with_name("fixtures")
WHEN = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)


CASES = [
    (TwseMarketConnector(), "twse_market.json", "2330"),
    (TpexMarketConnector(), "tpex_market.json", "6488.TWO"),
    (TwseInstitutionalConnector(), "twse_institutional.json", "2330.TW"),
    (TpexInstitutionalConnector(), "tpex_institutional.json", "6488"),
    (TwseMarginConnector(), "twse_margin.json", "2330"),
    (TpexMarginConnector(), "tpex_margin.json", "6488"),
]


class OfficialSourceTests(unittest.TestCase):
    def test_fixture_payloads_match_connector_contracts(self) -> None:
        for connector, fixture, symbol in CASES:
            with self.subTest(connector=connector.connector_id):
                payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
                result = connector.parse(payload, symbol=symbol, fetched_at=WHEN)
                self.assertEqual({item.field_id for item in result.observations}, connector.contract_fields)
                self.assertEqual({entry.field_id for entry in result.coverage}, connector.contract_fields)
                self.assertTrue(all(entry.status == "available" for entry in result.coverage))

    def test_roc_and_gregorian_source_dates_are_normalized_to_taipei(self) -> None:
        expected = datetime(2026, 10, 16, tzinfo=ZoneInfo("Asia/Taipei"))
        for connector, fixture, symbol in (
            (TpexMarketConnector(), "tpex_market.json", "6488"),
            (TpexInstitutionalConnector(), "tpex_institutional.json", "6488"),
        ):
            payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
            result = connector.parse(payload, symbol=symbol, fetched_at=WHEN)
            self.assertTrue(all(item.valid_time == expected for item in result.observations))

        payload = json.loads((FIXTURES / "twse_institutional.json").read_text(encoding="utf-8"))
        result = TwseInstitutionalConnector().parse(payload, symbol="2330", fetched_at=WHEN)
        self.assertTrue(
            all(item.valid_time == datetime(2026, 10, 15, tzinfo=ZoneInfo("Asia/Taipei")) for item in result.observations)
        )

    def test_twse_dealer_buy_and_sell_are_derived_from_own_and_hedging(self) -> None:
        payload = json.loads((FIXTURES / "twse_institutional.json").read_text(encoding="utf-8"))
        result = TwseInstitutionalConnector().parse(payload, symbol="2330", fetched_at=WHEN)
        values = {item.field_id: item.value for item in result.observations}
        self.assertEqual(values["institutional.dealer.buy"], 800000)
        self.assertEqual(values["institutional.dealer.sell"], 700000)

    def test_twse_margin_blank_numeric_cell_means_zero_not_missing(self) -> None:
        payload = json.loads((FIXTURES / "twse_margin.json").read_text(encoding="utf-8"))
        result = TwseMarginConnector().parse(payload, symbol="2330", fetched_at=WHEN)
        values = {item.field_id: item.value for item in result.observations}
        self.assertEqual(values["margin.short.stock_redemption"], 0)
        self.assertTrue(all(entry.status == "available" for entry in result.coverage))

    def test_source_missing_field_is_explicit_not_silent(self) -> None:
        payload = json.loads((FIXTURES / "twse_market.json").read_text(encoding="utf-8"))
        del payload[0]["Transaction"]
        result = TwseMarketConnector().parse(payload, symbol="2330", fetched_at=WHEN)
        item = next(entry for entry in result.coverage if entry.field_id == "market.transactions")
        self.assertEqual(item.status, "missing")
        self.assertEqual(item.reason, "field_absent_or_null")
        self.assertEqual(len(result.coverage), len(TwseMarketConnector().contract_fields))

    def test_wrong_payload_shape_fails_loudly(self) -> None:
        with self.assertRaisesRegex(ConnectorError, "頂層"):
            TwseMarketConnector().parse({"unexpected": []}, symbol="2330", fetched_at=WHEN)

    def test_fetch_failure_marks_every_contract_field_failed(self) -> None:
        connector = TwseMarketConnector()

        def broken_fetcher(_url: str, _timeout: float) -> bytes:
            raise ConnectorError("network blocked")

        result = connector.fetch_safe("2330", fetched_at=WHEN, fetcher=broken_fetcher)
        self.assertEqual({item.field_id for item in result.coverage}, connector.contract_fields)
        self.assertTrue(all(item.status == "failed" for item in result.coverage))
        self.assertEqual(result.error, "network blocked")
