from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tw_stock_swing.coverage import CoverageEntry
from tw_stock_swing.storage import ResearchDatabase


UTC = timezone.utc


def _database() -> ResearchDatabase:
    database = ResearchDatabase()
    database.migrate()
    database.upsert_source("twse_openapi", "TWSE", "official")
    return database


class StorageTests(unittest.TestCase):
    def test_observation_revisions_are_append_only(self) -> None:
      with _database() as database:
        instrument = database.add_instrument("2330", "TWSE", "台積電", datetime(2020, 1, 1, tzinfo=UTC))
        first = database.add_observation(
            instrument_id=instrument, field_id="market.close", value=1000, unit="TWD/share",
            valid_time=datetime(2026, 10, 15, tzinfo=UTC), known_at=datetime(2026, 10, 15, 6, tzinfo=UTC),
            system_recorded_at=datetime(2026, 10, 15, 6, 1, tzinfo=UTC), source_id="twse_openapi",
        )
        second = database.add_observation(
            instrument_id=instrument, field_id="market.close", value=1005, unit="TWD/share",
            valid_time=datetime(2026, 10, 15, tzinfo=UTC), known_at=datetime(2026, 10, 15, 7, tzinfo=UTC),
            system_recorded_at=datetime(2026, 10, 15, 7, 1, tzinfo=UTC), source_id="twse_openapi", supersedes=first,
        )
        rows = database.connection.execute(
            "SELECT observation_id, revision FROM observations ORDER BY revision"
        ).fetchall()
        self.assertEqual([(row["observation_id"], row["revision"]) for row in rows], [(first, 1), (second, 2)])


    def test_as_of_query_excludes_later_known_data(self) -> None:
      with _database() as database:
        instrument = database.add_instrument("2330", "TWSE", "台積電", datetime(2020, 1, 1, tzinfo=UTC))
        database.add_observation(
            instrument_id=instrument, field_id="market.close", value=1000, unit="TWD/share",
            valid_time=datetime(2026, 10, 15, tzinfo=UTC), known_at=datetime(2026, 10, 15, 6, tzinfo=UTC),
            system_recorded_at=datetime(2026, 10, 15, 6, 1, tzinfo=UTC), source_id="twse_openapi",
        )
        database.add_observation(
            instrument_id=instrument, field_id="market.close", value=1005, unit="TWD/share",
            valid_time=datetime(2026, 10, 15, tzinfo=UTC), known_at=datetime(2026, 10, 15, 8, tzinfo=UTC),
            system_recorded_at=datetime(2026, 10, 15, 8, 1, tzinfo=UTC), source_id="twse_openapi",
        )
        earlier = database.observations_as_of(instrument, datetime(2026, 10, 15, 7, tzinfo=UTC))
        later = database.observations_as_of(instrument, datetime(2026, 10, 15, 9, tzinfo=UTC))
        self.assertEqual([item.value_number for item in earlier], [1000])
        self.assertEqual([item.value_number for item in later], [1005])


    def test_evidence_ledger_preserves_quality_dimensions(self) -> None:
      with _database() as database:
        run = database.start_run("2330", datetime(2026, 10, 16, tzinfo=UTC), "TWSE")
        quality = {
            "source_reliability": 0.95, "claim_support": 0.9, "independence": 1.0,
            "recency": 0.9, "relevance": 0.8, "directness": 1.0, "corroboration": 0.7,
        }
        evidence_id = database.add_evidence(
            run_id=run, statement="外資買超", claim_type="FACT", stance="supporting",
            source_id="twse_openapi", retrieved_at=datetime(2026, 10, 16, tzinfo=UTC), quality=quality,
        )
        row = database.connection.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        self.assertEqual({name: row[name] for name in quality}, quality)


    def test_coverage_primary_key_prevents_silent_duplicate(self) -> None:
      with _database() as database:
        run = database.start_run("2330", datetime(2026, 10, 16, tzinfo=UTC), "TWSE")
        database.save_coverage(run, [CoverageEntry("market.close", "available", "fixture")])
        count = database.connection.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]
        self.assertEqual(count, 1)
