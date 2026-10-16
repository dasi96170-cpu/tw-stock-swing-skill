from __future__ import annotations

import unittest

from tw_stock_swing.catalog import load_catalog
from tw_stock_swing.coverage import CoverageEntry, CoverageError, assert_strict_p0_coverage, complete_coverage


class CoverageTests(unittest.TestCase):
    def test_coverage_never_silently_omits_catalog_items(self) -> None:
        catalog = load_catalog()
        entries = complete_coverage(
            catalog,
            [CoverageEntry("market.close", "available", "fixture", "twse_openapi")],
        )
        self.assertEqual(len(entries), len(catalog.fields))
        self.assertEqual({entry.field_id for entry in entries}, set(catalog.by_id))
        self.assertEqual(len({entry.field_id for entry in entries}), len(entries))
        self.assertEqual(
            next(entry for entry in entries if entry.field_id == "category.news").reason,
            "not_implemented",
        )

    def test_missing_p0_field_fails_strict_coverage(self) -> None:
        catalog = load_catalog()
        entries = complete_coverage(catalog, [])
        with self.assertRaisesRegex(CoverageError, "P0 資料不完整"):
            assert_strict_p0_coverage(catalog, entries)

    def test_unknown_and_duplicate_coverage_are_rejected(self) -> None:
        catalog = load_catalog()
        with self.assertRaisesRegex(CoverageError, "未知"):
            complete_coverage(catalog, [CoverageEntry("not.real", "missing", "test")])
        duplicate = CoverageEntry("market.close", "available", "test")
        with self.assertRaisesRegex(CoverageError, "重複"):
            complete_coverage(catalog, [duplicate, duplicate])
