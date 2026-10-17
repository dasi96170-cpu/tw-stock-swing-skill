from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from .analysis import analyze_bars, expected_completed_session
from .catalog import ResearchCatalog, load_catalog
from .coverage import CoverageEntry, complete_coverage, summarize_by_category
from .models import AnalysisResult
from .providers import YahooFinanceProvider
from .sources import ConnectorResult, NormalizedObservation, OFFICIAL_CONNECTORS
from .storage import ResearchDatabase


ACTIVE_CONNECTORS = {
    "TWSE": ("twse_market", "twse_institutional", "twse_margin"),
    "TPEx": ("tpex_market", "tpex_institutional", "tpex_margin"),
}


class ResearchConnector(Protocol):
    connector_id: str
    source_id: str
    endpoint: str

    def fetch_safe(self, symbol: str, *, timeout: float = 20.0) -> ConnectorResult: ...


@dataclass(frozen=True, slots=True)
class SourceHealth:
    connector_id: str
    source_id: str
    endpoint: str
    status: str
    fetched_at: datetime
    observations: int
    missing_fields: tuple[str, ...] = ()
    valid_times: tuple[datetime, ...] = ()
    error: str | None = None
    content_hash: str | None = None
    valid_time_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "source_id": self.source_id,
            "endpoint": self.endpoint,
            "status": self.status,
            "fetched_at": self.fetched_at.isoformat(),
            "observations": self.observations,
            "missing_fields": list(self.missing_fields),
            "valid_times": [value.isoformat() for value in self.valid_times],
            "error": self.error,
            "content_hash": self.content_hash,
            "valid_time_verified": self.valid_time_verified,
        }


@dataclass(frozen=True, slots=True)
class CoverageItem:
    field_id: str
    name: str
    category: str
    priority: str
    implementation: str
    status: str
    reason: str
    source_id: str | None
    observed_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "name": self.name,
            "category": self.category,
            "priority": self.priority,
            "implementation": self.implementation,
            "status": self.status,
            "reason": self.reason,
            "source_id": self.source_id,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }


@dataclass(frozen=True, slots=True)
class ResearchReport:
    status: str
    generated_at: datetime
    symbol: str
    name: str
    market: str
    analysis: AnalysisResult
    official_observations: tuple[NormalizedObservation, ...]
    official_findings: tuple[str, ...]
    source_health: tuple[SourceHealth, ...]
    coverage: tuple[CoverageItem, ...]
    coverage_by_category: dict[str, dict[str, int]]
    decision_blockers: tuple[str, ...]
    market_conflicts: tuple[str, ...]
    warnings: tuple[str, ...]
    run_id: str | None = None
    database_path: str | None = None

    @property
    def observations_by_id(self) -> dict[str, NormalizedObservation]:
        return {item.field_id: item for item in self.official_observations}

    @property
    def missing_p0_categories(self) -> tuple[CoverageItem, ...]:
        passing = {"available", "computed", "not_applicable"}
        return tuple(
            item
            for item in self.coverage
            if item.field_id.startswith("category.")
            and item.priority == "P0"
            and (item.implementation != "implemented" or item.status not in passing)
        )

    @property
    def decision_eligible(self) -> bool:
        return self.status == "complete" and not self.decision_blockers

    def to_dict(self) -> dict[str, Any]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for observation in self.official_observations:
            category = observation.field_id.split(".", 1)[0]
            grouped.setdefault(category, {})[observation.field_id] = {
                "value": observation.value,
                "unit": observation.unit,
                "valid_time": observation.valid_time.isoformat(),
                "source_id": observation.source_id,
                "source_record_key": observation.source_record_key,
            }

        status_counts: dict[str, int] = {}
        for item in self.coverage:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1

        analysis_payload = self.analysis.to_dict()
        if not self.decision_eligible:
            for field in (
                "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
                "risk_reward_1",
            ):
                analysis_payload[field] = None

        return {
            "status": self.status,
            "decision_eligible": self.decision_eligible,
            "decision_blockers": list(self.decision_blockers),
            "scope": "market_technical_institutional_margin",
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "analysis": analysis_payload,
            "official": grouped,
            "official_findings": list(self.official_findings),
            "sources": [source.to_dict() for source in self.source_health],
            "coverage": {
                "summary": status_counts,
                "by_category": self.coverage_by_category,
                "missing_p0_categories": [item.to_dict() for item in self.missing_p0_categories],
                "items": [item.to_dict() for item in self.coverage],
            },
            "warnings": list(self.warnings),
            "market_conflicts": list(self.market_conflicts),
            "persistence": {
                "run_id": self.run_id,
                "database": self.database_path,
                "saved": self.run_id is not None,
            },
            "disclaimer": "僅供研究與決策輔助；不是即時報價、投資建議或獲利保證。",
        }


def _market_for_symbol(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".TWO"):
        return "TPEx"
    if normalized.endswith(".TW"):
        return "TWSE"
    raise ValueError(f"資料源未回傳可辨識的台股市場代號: {symbol}")


def _health(result: ConnectorResult, generated_at: datetime) -> SourceHealth:
    missing = tuple(item.field_id for item in result.coverage if item.status != "available")
    valid_times = tuple(sorted({item.valid_time for item in result.observations}))
    expected = expected_completed_session(generated_at)
    latest = valid_times[-1].astimezone(ZoneInfo("Asia/Taipei")).date() if valid_times else None
    stale = latest is not None and latest < expected
    future = latest is not None and latest > generated_at.astimezone(ZoneInfo("Asia/Taipei")).date()
    status = (
        "failed" if result.error else
        "partial" if missing else
        "unverified_time" if not result.valid_time_verified else
        "future" if future else
        "stale" if stale else
        "ok"
    )
    return SourceHealth(
        connector_id=result.connector_id,
        source_id=result.source_id,
        endpoint=result.endpoint,
        status=status,
        fetched_at=result.fetched_at,
        observations=len(result.observations),
        missing_fields=missing,
        valid_times=valid_times,
        error=result.error,
        content_hash=result.content_hash,
        valid_time_verified=result.valid_time_verified,
    )


def _gate_entry(
    field_id: str,
    result: ConnectorResult,
    evaluated_coverage: tuple[CoverageEntry, ...],
) -> CoverageEntry:
    statuses = {item.status for item in evaluated_coverage}
    if result.error or "failed" in statuses:
        status, reason = "failed", result.error or "official_fetch_failed"
    elif "stale" in statuses:
        status, reason = "stale", "official_source_stale"
    elif "unverified" in statuses:
        status, reason = "unverified", "official_source_time_unverified"
    elif "conflicting" in statuses:
        status, reason = "conflicting", "official_source_conflict"
    elif statuses == {"available"}:
        status, reason = "available", "implemented_subset_available"
    else:
        status, reason = "missing", "official_fields_incomplete"
    return CoverageEntry(field_id, status, reason, result.source_id, result.fetched_at)


def _coverage_items(
    catalog: ResearchCatalog,
    results: tuple[ConnectorResult, ...],
    generated_at: datetime,
) -> tuple[tuple[CoverageItem, ...], dict[str, dict[str, int]]]:
    market_result, institutional_result, margin_result = results
    evaluated: list[tuple[CoverageEntry, ...]] = []
    for result in results:
        health = _health(result, generated_at)
        replacement_status = {
            "stale": "stale",
            "future": "unverified",
            "unverified_time": "unverified",
        }.get(health.status)
        evaluated.append(
            tuple(
                CoverageEntry(
                    entry.field_id,
                    replacement_status if replacement_status and entry.status == "available" else entry.status,
                    health.status if replacement_status and entry.status == "available" else entry.reason,
                    entry.source_id,
                    entry.observed_at,
                )
                for entry in result.coverage
            )
        )
    market_coverage, institutional_coverage, margin_coverage = evaluated
    supplied = [entry for entries in evaluated for entry in entries]
    supplied.extend(
        (
            _gate_entry("category.identity", market_result, market_coverage),
            _gate_entry("category.market", market_result, market_coverage),
            CoverageEntry(
                "category.technical",
                "computed",
                "deterministic_daily_indicators",
                "computed",
                generated_at,
            ),
            _gate_entry("category.institutional", institutional_result, institutional_coverage),
            _gate_entry("category.margin", margin_result, margin_coverage),
        )
    )
    completed = complete_coverage(catalog, supplied)
    definitions = catalog.by_id
    items = tuple(
        CoverageItem(
            field_id=entry.field_id,
            name=definitions[entry.field_id].name,
            category=definitions[entry.field_id].category,
            priority=definitions[entry.field_id].priority,
            implementation=definitions[entry.field_id].implementation,
            status=entry.status,
            reason=entry.reason,
            source_id=entry.source_id,
            observed_at=entry.observed_at,
        )
        for entry in completed
    )
    return items, summarize_by_category(catalog, completed)


def _official_findings(
    observations: tuple[NormalizedObservation, ...],
) -> tuple[str, ...]:
    values = {item.field_id: item.value for item in observations}
    findings: list[str] = []
    institutions = (
        ("institutional.foreign.net", "外資"),
        ("institutional.trust.net", "投信"),
        ("institutional.dealer.net", "自營商"),
    )
    for field_id, label in institutions:
        raw = values.get(field_id)
        if not isinstance(raw, (int, float)):
            continue
        direction = "買超" if raw > 0 else "賣超" if raw < 0 else "買賣超持平"
        amount = f" {abs(raw):,.0f} 股" if raw else ""
        findings.append(f"{label}單日{direction}{amount}")

    foreign = values.get("institutional.foreign.net")
    trust = values.get("institutional.trust.net")
    if isinstance(foreign, (int, float)) and isinstance(trust, (int, float)):
        if foreign > 0 and trust > 0:
            findings.append("外資與投信單日同向買超")
        elif foreign < 0 and trust < 0:
            findings.append("外資與投信單日同向賣超")

    balances = (
        ("margin.financing.balance", "margin.financing.previous_balance", "融資"),
        ("margin.short.balance", "margin.short.previous_balance", "融券"),
    )
    for current_id, previous_id, label in balances:
        current = values.get(current_id)
        previous = values.get(previous_id)
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            continue
        change = current - previous
        direction = "增加" if change > 0 else "減少" if change < 0 else "持平"
        amount = f" {abs(change):,.0f} 張" if change else ""
        findings.append(f"{label}餘額較前日{direction}{amount}")
    return tuple(findings)


def _market_conflicts(
    analysis: AnalysisResult,
    observations: tuple[NormalizedObservation, ...],
) -> tuple[str, ...]:
    conflicts: list[str] = []
    official_close = next(
        (item for item in observations if item.field_id == "market.close"),
        None,
    )
    if official_close is None or not isinstance(official_close.value, (int, float)):
        return ()
    official_date = official_close.valid_time.astimezone(ZoneInfo("Asia/Taipei")).date()
    if official_date != analysis.data_date:
        return ()
    tolerance = max(0.01, abs(float(official_close.value)) * 0.001)
    difference = abs(float(official_close.value) - analysis.close)
    if difference > tolerance:
        conflicts.append(
            "同一交易日收盤價衝突："
            f"官方 {float(official_close.value):g}、Yahoo {analysis.close:g}"
        )
    return tuple(conflicts)


def research_stock(
    symbol: str,
    *,
    timeout: float = 20.0,
    provider: YahooFinanceProvider | None = None,
    connectors: Mapping[str, ResearchConnector] | None = None,
    catalog: ResearchCatalog | None = None,
    generated_at: datetime | None = None,
    database_path: str | Path | None = None,
) -> ResearchReport:
    """Build the first usable single-stock report from live and computed data."""

    if timeout <= 0:
        raise ValueError("timeout 必須大於 0")
    when = generated_at or datetime.now(ZoneInfo("Asia/Taipei"))
    if when.tzinfo is None:
        raise ValueError("generated_at 必須包含時區")

    active_provider = provider if provider is not None else YahooFinanceProvider(timeout=timeout)
    market_data = active_provider.fetch(symbol)
    yahoo_fetched_at = datetime.now(timezone.utc)
    analysis = analyze_bars(market_data, as_of=when)
    market = _market_for_symbol(market_data.symbol)
    base_symbol = market_data.symbol.split(".", 1)[0]
    active_connectors = connectors if connectors is not None else OFFICIAL_CONNECTORS
    connector_ids = ACTIVE_CONNECTORS[market]
    selected = tuple(active_connectors[connector_id] for connector_id in connector_ids)

    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = [
            executor.submit(connector.fetch_safe, base_symbol, timeout=timeout)
            for connector in selected
        ]
        results = tuple(future.result() for future in futures)

    observations = tuple(item for result in results for item in result.observations)
    official_name = next(
        (
            str(item.value)
            for item in observations
            if item.field_id == "identity.name" and str(item.value).strip()
        ),
        analysis.name,
    )
    official_health = tuple(_health(result, when) for result in results)
    yahoo_status = analysis.freshness_status
    if yahoo_status == "current":
        yahoo_status = "ok"
    yahoo_health = SourceHealth(
        connector_id="yahoo_daily",
        source_id="yahoo_finance",
        endpoint="Yahoo Finance Chart API",
        status=yahoo_status,
        fetched_at=yahoo_fetched_at,
        observations=len(market_data.bars),
        valid_times=(
            datetime.combine(
                analysis.data_date,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Taipei"),
            ),
        ),
        valid_time_verified=True,
    )

    active_catalog = catalog if catalog is not None else load_catalog()
    coverage, by_category = _coverage_items(active_catalog, results, when)
    conflicts = _market_conflicts(analysis, observations)
    if conflicts:
        coverage = tuple(
            replace(
                item,
                status="conflicting",
                reason="official_yahoo_same_session_close_conflict",
            )
            if item.field_id in {"category.market", "market.close"}
            else item
            for item in coverage
        )
        by_category = {}
        for item in coverage:
            counts = by_category.setdefault(item.category, {})
            counts[item.status] = counts.get(item.status, 0) + 1
    warnings: list[str] = []
    if analysis.freshness_warning:
        warnings.append(analysis.freshness_warning)
    for source in official_health:
        if source.status == "failed":
            warnings.append(f"{source.connector_id} 失敗：{source.error}")
        elif source.status == "partial":
            warnings.append(
                f"{source.connector_id} 缺少欄位：{', '.join(source.missing_fields)}"
            )
        elif source.status == "stale":
            warnings.append(
                f"{source.connector_id} 最新資料為 {source.valid_times[-1].date().isoformat()}，可能已過期"
            )
        elif source.status == "unverified_time":
            warnings.append(f"{source.connector_id} 回應缺少可驗證資料日期")
    missing_p0_items = [
        item for item in coverage
        if item.field_id.startswith("category.")
        and item.priority == "P0"
        and (
            item.implementation != "implemented"
            or item.status not in {"available", "computed", "not_applicable"}
        )
    ]
    missing_p0_names = [item.name for item in missing_p0_items]
    if missing_p0_names:
        warnings.append("尚未涵蓋的 P0 類別：" + "、".join(missing_p0_names))

    warnings.extend(conflicts)
    blockers: list[str] = []
    blockers.extend(f"P0 類別未就緒：{item.name}" for item in missing_p0_items)
    if analysis.freshness_status != "current":
        blockers.append(f"Yahoo 日線狀態為 {analysis.freshness_status}")
    blockers.extend(conflicts)
    for source in official_health:
        if source.status in {"failed", "future", "unverified_time"}:
            blockers.append(f"{source.connector_id} 狀態為 {source.status}")

    active_status = "insufficient" if blockers else "complete"
    if active_status == "complete" and (
        warnings or any(source.status != "ok" for source in official_health)
    ):
        active_status = "partial"
    report = ResearchReport(
        status=active_status,
        generated_at=when,
        symbol=market_data.symbol,
        name=official_name,
        market=market,
        analysis=analysis,
        official_observations=observations,
        official_findings=_official_findings(observations),
        source_health=(yahoo_health, *official_health),
        coverage=coverage,
        coverage_by_category=by_category,
        decision_blockers=tuple(dict.fromkeys(blockers)),
        market_conflicts=conflicts,
        warnings=tuple(warnings),
    )
    if database_path is not None:
        run_id = persist_research_report(report, database_path)
        report = replace(report, run_id=run_id, database_path=str(database_path))
    return report


_EVIDENCE_QUALITY_OFFICIAL = {
    "source_reliability": 0.95,
    "claim_support": 1.0,
    "independence": 1.0,
    "recency": 0.9,
    "relevance": 1.0,
    "directness": 1.0,
    "corroboration": 0.5,
}


def persist_research_report(report: ResearchReport, path: str | Path) -> str:
    """Persist one report with bitemporal observations and linked evidence."""

    with ResearchDatabase(path) as database:
        database.migrate()
        database.upsert_source(
            "twse_openapi", "臺灣證券交易所官方資料服務", "official",
            "https://openapi.twse.com.tw/v1",
        )
        database.upsert_source(
            "tpex_openapi", "證券櫃檯買賣中心 OpenAPI", "official",
            "https://www.tpex.org.tw/openapi/v1",
        )
        database.upsert_source(
            "yahoo_finance", "Yahoo Finance Chart API", "third_party",
            "https://query1.finance.yahoo.com/v8/finance/chart",
        )
        database.upsert_source("computed", "Python 可重現計算", "internal")

        base_symbol = report.symbol.split(".", 1)[0]
        instrument_id = database.get_or_create_instrument(
            base_symbol,
            report.market,
            report.name,
            report.generated_at,
        )
        run_id = database.start_run(
            base_symbol,
            report.generated_at,
            report.market,
            depth="first_batch",
        )
        try:
            document_by_prefix: dict[str, str] = {}
            fetched_at_by_prefix: dict[str, datetime] = {}
            yahoo_retrieved_at = report.generated_at
            for source in report.source_health:
                if source.connector_id == "yahoo_daily":
                    yahoo_retrieved_at = source.fetched_at
                if "market" in source.connector_id:
                    fetched_at_by_prefix["identity"] = source.fetched_at
                    fetched_at_by_prefix["market"] = source.fetched_at
                elif "institutional" in source.connector_id:
                    fetched_at_by_prefix["institutional"] = source.fetched_at
                elif "margin" in source.connector_id:
                    fetched_at_by_prefix["margin"] = source.fetched_at
                if source.content_hash is None:
                    continue
                document_id = database.add_raw_document(
                    source_id=source.source_id,
                    url=source.endpoint,
                    content_hash=source.content_hash,
                    retrieved_at=source.fetched_at,
                    event_time=source.valid_times[-1] if source.valid_times else None,
                    title=source.connector_id,
                )
                if "market" in source.connector_id:
                    document_by_prefix["identity"] = document_id
                    document_by_prefix["market"] = document_id
                elif "institutional" in source.connector_id:
                    document_by_prefix["institutional"] = document_id
                elif "margin" in source.connector_id:
                    document_by_prefix["margin"] = document_id

            observation_ids: dict[tuple[str, str], str] = {}
            for observation in report.official_observations:
                prefix = observation.field_id.split(".", 1)[0]
                observation_id = database.add_observation(
                    instrument_id=instrument_id,
                    field_id=observation.field_id,
                    value=observation.value,
                    unit=observation.unit,
                    valid_time=observation.valid_time,
                    known_at=fetched_at_by_prefix.get(prefix, report.generated_at),
                    source_id=observation.source_id,
                    document_id=document_by_prefix.get(prefix),
                    source_record_key=observation.source_record_key,
                )
                observation_ids[(observation.field_id, observation.source_id)] = observation_id

            yahoo_valid_time = datetime.combine(
                report.analysis.data_date,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Taipei"),
            )
            yahoo_close_id = database.add_observation(
                instrument_id=instrument_id,
                field_id="market.close",
                value=report.analysis.close,
                unit="TWD/share",
                valid_time=yahoo_valid_time,
                known_at=yahoo_retrieved_at,
                source_id="yahoo_finance",
                source_record_key=report.symbol,
            )

            coverage_rows = (
                CoverageEntry(
                    item.field_id,
                    item.status,
                    item.reason,
                    item.source_id,
                    item.observed_at,
                )
                for item in report.coverage
            )
            database.save_coverage(run_id, coverage_rows)

            evidence_ids: list[str] = []
            for observation in report.official_observations:
                evidence_ids.append(
                    database.add_evidence(
                        run_id=run_id,
                        statement=f"{observation.field_id} = {observation.value} {observation.unit}",
                        claim_type="FACT",
                        stance="neutral",
                        source_id=observation.source_id,
                        retrieved_at=fetched_at_by_prefix.get(
                            observation.field_id.split(".", 1)[0],
                            report.generated_at,
                        ),
                        event_time=observation.valid_time,
                        quality=_EVIDENCE_QUALITY_OFFICIAL,
                        document_id=document_by_prefix.get(
                            observation.field_id.split(".", 1)[0]
                        ),
                        observation_ids=(
                            observation_ids[(observation.field_id, observation.source_id)],
                        ),
                    )
                )
            evidence_ids.append(
                database.add_evidence(
                    run_id=run_id,
                    statement=(
                        f"Yahoo 日線 {report.analysis.data_date.isoformat()} 收盤 "
                        f"{report.analysis.close:g}"
                    ),
                    claim_type="FACT",
                    stance="neutral",
                    source_id="yahoo_finance",
                    retrieved_at=yahoo_retrieved_at,
                    event_time=yahoo_valid_time,
                    quality={**_EVIDENCE_QUALITY_OFFICIAL, "source_reliability": 0.7},
                    observation_ids=(yahoo_close_id,),
                )
            )
            database.add_conclusion(
                run_id,
                "decision_eligibility",
                "eligible" if report.decision_eligible else "blocked",
                "medium" if report.decision_eligible else "low",
                evidence_ids,
                details={
                    "status": report.status,
                    "decision_blockers": list(report.decision_blockers),
                    "market_conflicts": list(report.market_conflicts),
                },
            )
            database.finish_run(run_id, report.status)
        except Exception:
            database.finish_run(run_id, "failed")
            raise
    return run_id
