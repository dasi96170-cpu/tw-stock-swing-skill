from __future__ import annotations

from pathlib import Path
import unittest

from tw_stock_swing.catalog import load_catalog, load_source_catalog, load_traceability
from tw_stock_swing.sources import OFFICIAL_CONNECTORS


EXPECTED_CATEGORIES = {
    "identity", "market", "volume_structure", "technical", "institutional", "margin",
    "ownership", "revenue", "income_statement", "balance_sheet", "cash_flow",
    "financial_quality", "valuation", "operations", "corporate_actions", "governance",
    "material_events", "news", "supply_chain", "industry_specific", "peers",
    "macro_derivatives", "alternative",
}


class CatalogTests(unittest.TestCase):
    def test_catalog_has_all_23_requirement_categories(self) -> None:
        catalog = load_catalog()
        gates = {field.category for field in catalog.fields if field.kind == "category_gate"}
        self.assertEqual(gates, EXPECTED_CATEGORIES)
        self.assertEqual(len(gates), 23)

    def test_catalog_fields_are_unique_and_valid(self) -> None:
        catalog = load_catalog()
        ids = [field.id for field in catalog.fields]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(catalog.version)
        self.assertIn("missing", catalog.allowed_statuses)
        self.assertTrue(all(field.sources for field in catalog.fields))

    def test_connector_contract_fields_exist_in_catalog(self) -> None:
        catalog_ids = set(load_catalog().by_id)
        for connector in OFFICIAL_CONNECTORS.values():
            self.assertLessEqual(connector.contract_fields, catalog_ids)

    def test_source_catalog_has_official_endpoints(self) -> None:
        data = load_source_catalog()
        sources = {source["id"]: source for source in data["sources"]}
        self.assertLessEqual({"twse_openapi", "tpex_openapi"}, sources.keys())
        endpoint_ids = {
            endpoint["id"]
            for source_id in ("twse_openapi", "tpex_openapi")
            for endpoint in sources[source_id]["endpoints"]
        }
        self.assertEqual(endpoint_ids, {
            "twse.market.daily_all", "twse.institutional.daily", "twse.margin.daily",
            "tpex.market.daily_close", "tpex.institutional.daily", "tpex.margin.daily",
        })

    def test_traceability_references_real_files_and_catalog_items(self) -> None:
        root = Path(__file__).resolve().parents[1]
        catalog_ids = tuple(load_catalog().by_id)
        data = load_traceability()
        self.assertTrue(data["requirements"])
        for requirement in data["requirements"]:
            for implementation in requirement["implementation"]:
                self.assertTrue((root / implementation).exists(), implementation)
            for prefix in requirement["catalog_prefixes"]:
                self.assertTrue(any(item.startswith(prefix) for item in catalog_ids), prefix)
