from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from .coverage import CoverageEntry


SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    authority TEXT NOT NULL,
    base_url TEXT,
    terms_url TEXT,
    storage_policy TEXT,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    name TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    system_recorded_at TEXT NOT NULL,
    UNIQUE(symbol, market, valid_from, system_recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments(symbol, market);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT,
    as_of TEXT NOT NULL,
    depth TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS raw_documents (
    document_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    url TEXT NOT NULL,
    title TEXT,
    content_hash TEXT NOT NULL,
    media_type TEXT,
    event_time TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    parser_version TEXT,
    storage_status TEXT NOT NULL,
    UNIQUE(source_id, url, content_hash)
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    field_id TEXT NOT NULL,
    value_number REAL,
    value_text TEXT,
    unit TEXT NOT NULL,
    valid_time TEXT NOT NULL,
    known_at TEXT NOT NULL,
    system_recorded_at TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    document_id TEXT REFERENCES raw_documents(document_id),
    source_record_key TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    supersedes_observation_id TEXT REFERENCES observations(observation_id),
    CHECK(value_number IS NOT NULL OR value_text IS NOT NULL),
    UNIQUE(instrument_id, field_id, source_id, valid_time, revision)
);

CREATE INDEX IF NOT EXISTS idx_observations_asof
ON observations(instrument_id, field_id, known_at, valid_time, revision);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    statement TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK(claim_type IN
      ('FACT','EXPECTATION','INTERPRETATION','INFERENCE','RUMOR','UNKNOWN','CONFLICT')),
    stance TEXT NOT NULL CHECK(stance IN ('supporting','opposing','neutral')),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    document_id TEXT REFERENCES raw_documents(document_id),
    event_time TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    source_reliability REAL NOT NULL,
    claim_support REAL NOT NULL,
    independence REAL NOT NULL,
    recency REAL NOT NULL,
    relevance REAL NOT NULL,
    directness REAL NOT NULL,
    corroboration REAL NOT NULL,
    evidence_status TEXT NOT NULL,
    CHECK(source_reliability BETWEEN 0 AND 1),
    CHECK(claim_support BETWEEN 0 AND 1),
    CHECK(independence BETWEEN 0 AND 1),
    CHECK(recency BETWEEN 0 AND 1),
    CHECK(relevance BETWEEN 0 AND 1),
    CHECK(directness BETWEEN 0 AND 1),
    CHECK(corroboration BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS coverage (
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    field_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
      ('available','computed','missing','stale','failed','restricted','not_applicable','conflicting','unverified')),
    reason TEXT NOT NULL,
    source_id TEXT REFERENCES sources(source_id),
    observed_at TEXT,
    PRIMARY KEY(run_id, field_id)
);

CREATE TABLE IF NOT EXISTS conclusions (
    conclusion_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    dimension TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence TEXT NOT NULL,
    rationale_json TEXT NOT NULL,
    system_recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_observations (
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    observation_id TEXT NOT NULL REFERENCES observations(observation_id),
    PRIMARY KEY(evidence_id, observation_id)
);

CREATE TABLE IF NOT EXISTS conclusion_evidence (
    conclusion_id TEXT NOT NULL REFERENCES conclusions(conclusion_id),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    PRIMARY KEY(conclusion_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS research_supplements (
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    connector_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    retrieved_at TEXT NOT NULL,
    source_date TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, connector_id)
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("時間必須包含時區")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    field_id: str
    value_number: float | None
    value_text: str | None
    unit: str
    valid_time: str
    known_at: str
    system_recorded_at: str
    source_id: str
    revision: int


class ResearchDatabase:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        # Normalize legacy offset strings on read, without rewriting evidence.
        self.connection.create_function("utc_iso", 1, _iso, deterministic=True)
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ResearchDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def migrate(self) -> None:
        with self.connection:
            self.connection.executescript(SCHEMA)
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _iso(utc_now())),
            )
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def upsert_source(
        self,
        source_id: str,
        name: str,
        authority: str,
        base_url: str = "",
        terms_url: str = "",
        storage_policy: str = "",
        checked_at: datetime | str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO sources
                   (source_id, name, authority, base_url, terms_url, storage_policy, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     name=excluded.name, authority=excluded.authority,
                     base_url=excluded.base_url, terms_url=excluded.terms_url,
                     storage_policy=excluded.storage_policy, checked_at=excluded.checked_at""",
                (
                    source_id,
                    name,
                    authority,
                    base_url,
                    terms_url,
                    storage_policy,
                    _iso(checked_at) if checked_at else None,
                ),
            )

    def add_instrument(
        self,
        symbol: str,
        market: str,
        name: str | None,
        valid_from: datetime | str,
        recorded_at: datetime | str | None = None,
    ) -> str:
        instrument_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """INSERT INTO instruments
                   (instrument_id, symbol, market, name, valid_from, system_recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (instrument_id, symbol, market, name, _iso(valid_from), _iso(recorded_at or utc_now())),
            )
        return instrument_id

    def get_or_create_instrument(
        self,
        symbol: str,
        market: str,
        name: str | None,
        valid_from: datetime | str,
        recorded_at: datetime | str | None = None,
    ) -> str:
        row = self.connection.execute(
            """SELECT instrument_id FROM instruments
               WHERE symbol=? AND market=? AND valid_to IS NULL
               ORDER BY system_recorded_at DESC LIMIT 1""",
            (symbol, market),
        ).fetchone()
        if row is not None:
            return str(row["instrument_id"])
        return self.add_instrument(symbol, market, name, valid_from, recorded_at)

    def start_run(
        self,
        symbol: str,
        as_of: datetime | str,
        market: str | None = None,
        depth: str = "standard",
    ) -> str:
        run_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """INSERT INTO research_runs
                   (run_id, symbol, market, as_of, depth, status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (run_id, symbol, market, _iso(as_of), depth, _iso(utc_now())),
            )
        return run_id

    def add_raw_document(
        self,
        *,
        source_id: str,
        url: str,
        content_hash: str,
        retrieved_at: datetime | str,
        title: str | None = None,
        media_type: str = "application/json",
        event_time: datetime | str | None = None,
        parser_version: str = "1",
        storage_status: str = "hash_only",
    ) -> str:
        existing = self.connection.execute(
            """SELECT document_id FROM raw_documents
               WHERE source_id=? AND url=? AND content_hash=?""",
            (source_id, url, content_hash),
        ).fetchone()
        if existing is not None:
            return str(existing["document_id"])
        document_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """INSERT INTO raw_documents
                   (document_id, source_id, url, title, content_hash, media_type,
                    event_time, retrieved_at, parser_version, storage_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id, source_id, url, title, content_hash, media_type,
                    _iso(event_time) if event_time else None, _iso(retrieved_at),
                    parser_version, storage_status,
                ),
            )
        return document_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        completed_at: datetime | str | None = None,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE research_runs SET status=?, completed_at=?
                   WHERE run_id=? AND completed_at IS NULL""",
                (status, _iso(completed_at or utc_now()), run_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"找不到執行中研究 run: {run_id}")

    def add_observation(
        self,
        *,
        instrument_id: str,
        field_id: str,
        value: float | int | str,
        unit: str,
        valid_time: datetime | str,
        known_at: datetime | str,
        source_id: str,
        system_recorded_at: datetime | str | None = None,
        document_id: str | None = None,
        source_record_key: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        observation_id = str(uuid4())
        valid = _iso(valid_time)
        revision_row = self.connection.execute(
            """SELECT observation_id, revision
               FROM observations
               WHERE instrument_id=? AND field_id=? AND source_id=? AND utc_iso(valid_time)=?""",
            (instrument_id, field_id, source_id, valid),
        ).fetchall()
        previous = max(revision_row, key=lambda row: int(row["revision"])) if revision_row else None
        revision = int(previous["revision"]) + 1 if previous is not None else 1
        if supersedes is None and previous is not None:
            supersedes = str(previous["observation_id"])
        if isinstance(value, bool) or (isinstance(value, (float, int)) and not math.isfinite(value)):
            raise ValueError("觀察數值必須有限且不可為布林值")
        number = float(value) if isinstance(value, (float, int)) else None
        text = value if isinstance(value, str) else None
        with self.connection:
            self.connection.execute(
                """INSERT INTO observations
                   (observation_id, instrument_id, field_id, value_number, value_text, unit,
                    valid_time, known_at, system_recorded_at, source_id, document_id,
                    source_record_key, revision, supersedes_observation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation_id,
                    instrument_id,
                    field_id,
                    number,
                    text,
                    unit,
                    valid,
                    _iso(known_at),
                    _iso(system_recorded_at or utc_now()),
                    source_id,
                    document_id,
                    source_record_key,
                    revision,
                    supersedes,
                ),
            )
        return observation_id

    def observations_as_of(
        self, instrument_id: str, as_of: datetime | str
    ) -> tuple[ObservationRecord, ...]:
        rows = self.connection.execute(
            """SELECT * FROM (
                 SELECT o.*,
                        ROW_NUMBER() OVER (
                          PARTITION BY field_id, source_id, utc_iso(valid_time)
                          ORDER BY revision DESC, utc_iso(system_recorded_at) DESC
                        ) AS row_rank
                 FROM observations o
                 WHERE instrument_id=? AND utc_iso(known_at)<=? AND utc_iso(system_recorded_at)<=?
               ) WHERE row_rank=1
               ORDER BY field_id, utc_iso(valid_time)""",
            (instrument_id, _iso(as_of), _iso(as_of)),
        ).fetchall()
        return tuple(
            ObservationRecord(
                observation_id=row["observation_id"],
                field_id=row["field_id"],
                value_number=row["value_number"],
                value_text=row["value_text"],
                unit=row["unit"],
                valid_time=_iso(row["valid_time"]),
                known_at=_iso(row["known_at"]),
                system_recorded_at=_iso(row["system_recorded_at"]),
                source_id=row["source_id"],
                revision=row["revision"],
            )
            for row in rows
        )

    def add_evidence(
        self,
        *,
        run_id: str,
        statement: str,
        claim_type: str,
        stance: str,
        source_id: str,
        retrieved_at: datetime | str,
        quality: dict[str, float],
        evidence_status: str = "available",
        event_time: datetime | str | None = None,
        published_at: datetime | str | None = None,
        document_id: str | None = None,
        observation_ids: Iterable[str] = (),
    ) -> str:
        dimensions = (
            "source_reliability", "claim_support", "independence", "recency",
            "relevance", "directness", "corroboration",
        )
        missing = [name for name in dimensions if name not in quality]
        if missing:
            raise ValueError("證據品質缺少維度: " + ", ".join(missing))
        evidence_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                f"""INSERT INTO evidence
                    (evidence_id, run_id, statement, claim_type, stance, source_id,
                     document_id, event_time, published_at, retrieved_at,
                     {', '.join(dimensions)}, evidence_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id, run_id, statement, claim_type, stance, source_id,
                    document_id, _iso(event_time) if event_time else None,
                    _iso(published_at) if published_at else None, _iso(retrieved_at),
                    *(quality[name] for name in dimensions), evidence_status,
                ),
            )
            self.connection.executemany(
                """INSERT INTO evidence_observations(evidence_id, observation_id)
                   VALUES (?, ?)""",
                ((evidence_id, observation_id) for observation_id in observation_ids),
            )
        return evidence_id

    def save_coverage(self, run_id: str, entries: Iterable[CoverageEntry]) -> None:
        rows = [
            (
                run_id, entry.field_id, entry.status, entry.reason, entry.source_id,
                _iso(entry.observed_at) if entry.observed_at else None,
            )
            for entry in entries
        ]
        with self.connection:
            self.connection.executemany(
                """INSERT INTO coverage
                   (run_id, field_id, status, reason, source_id, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def add_conclusion(
        self,
        run_id: str,
        dimension: str,
        verdict: str,
        confidence: str,
        evidence_ids: list[str],
        details: dict[str, Any] | None = None,
    ) -> str:
        conclusion_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """INSERT INTO conclusions
                   (conclusion_id, run_id, dimension, verdict, confidence,
                    rationale_json, system_recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    conclusion_id, run_id, dimension, verdict, confidence,
                    json.dumps(
                        {"evidence_ids": evidence_ids, **(details or {})},
                        ensure_ascii=False,
                    ),
                    _iso(utc_now()),
                ),
            )
            self.connection.executemany(
                """INSERT INTO conclusion_evidence(conclusion_id, evidence_id)
                   VALUES (?, ?)""",
                ((conclusion_id, evidence_id) for evidence_id in evidence_ids),
            )
        return conclusion_id

    def save_supplement(self, run_id: str, payload: dict[str, Any]) -> None:
        """Store normalized metadata with explicit date uncertainty, never full bodies."""
        with self.connection:
            self.connection.execute(
                """INSERT INTO research_supplements
                   (run_id, connector_id, source_id, retrieved_at, source_date, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, payload['connector_id'], payload['source_id'], _iso(payload['fetched_at']),
                 payload['source_date'], json.dumps(payload, ensure_ascii=False, allow_nan=False)),
            )
